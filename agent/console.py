"""Local-only SaaS prototype adapter. Django will own these APIs in production."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from .frontend import FRONTEND_ROOT
from threading import Thread, Lock, Event, BoundedSemaphore
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
from .runtime import execute_run
from .schemas import RunInput, Persona
from .policies import ShopScriptedPolicy
from .shop import shop_server
from .skyvern_bridge import SkyvernBridge, SkyvernUnavailable
from .persona_dashboard import PersonaDashboard
from .persona_report import build_report, markdown_report
from .preflight import PreflightPlanner
from .session_explain import explain_session
from .report_pdf import render_pdf

ROOT = Path(__file__).resolve().parent.parent
WEB = FRONTEND_ROOT / "saas-web"


class ConsoleStore:
    def __init__(self, artifacts=None):
        self.artifacts = Path(artifacts or ROOT / "artifacts")
        self.lock = Lock()
        self.runs = {}
        self.files = {}
        self.load()
        self.skyvern = SkyvernBridge(self.artifacts)
        self.personas = PersonaDashboard(self.artifacts)
        self.planner = PreflightPlanner()
        self.pdf_slots = BoundedSemaphore(1)

    def artifact_id(self, filename):
        path = Path(filename).resolve()
        if not path.is_file() or not path.is_relative_to(self.artifacts.resolve()) or path.suffix != ".png":
            return None
        token = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        self.files[token] = path
        return token

    def public_run(self, summary, source, metadata=None):
        metadata = metadata or {}
        sessions = []
        for s in summary.get("sessions", []):
            issues = []
            for issue in s.get("issues", []):
                issues.append({"category": issue.get("category", "unknown"),
                    "summary": issue.get("summary", ""), "evidence_step_ids": issue.get("evidence_step_ids", []),
                    "observed_fields": issue.get("observed_fields"), "configured_limit": issue.get("configured_limit"),
                    "artifact_id": self.artifact_id(issue.get("artifact") or "")})
            sessions.append({"id": s["session_id"], "persona_id": s["persona_id"], "status": s["status"],
                "steps": s["steps"], "duration_seconds": s["duration_seconds"], "issues": issues,
                "goal":s.get("goal"), "reason":s.get("reason"), "task_id":s.get("task_id"),
                "artifact_id": self.artifact_id(s.get("final_artifact") or "")})
        project_id = summary.get("project_id", metadata.get("project_id", "banchan"))
        if project_id != "banchan":
            raise ValueError("Unsupported project")
        name = summary.get("run_id", "")
        variant = metadata.get("variant", "problem" if "problem" in name else "fixed")
        case = metadata.get("case", name.rsplit("_", 1)[-1])
        if case not in {"all", "signup", "fee", "cta"}:
            case = "all"
        return {"id": metadata.get("job_id", summary["attempt_id"]), "project_id": project_id, "project_name": "반찬로드",
            "status": "completed", "variant": variant, "case": case,
            "created_at": metadata.get("created_at", datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat()),
            "policy": "scripted_fixture", "label": ("문제 버전" if variant == "problem" else "개선 버전"),
            "plan": summary.get("discovery", {}).get("tasks", []), "readiness": summary.get("readiness"),
            "sessions": sessions, "progress": {"finished": len(sessions), "total": len(sessions)},
            "events": [], "error": None}

    def load(self):
        for folder in [self.artifacts / "shop-evaluation", self.artifacts / "console-runs"]:
            for path in folder.rglob("summary.json") if folder.exists() else []:
                try:
                    summary = json.loads(path.read_text(encoding="utf-8"))
                    meta_path = path.parent / "console.json"
                    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                    run = self.public_run(summary, path, metadata)
                    self.runs[run["id"]] = run
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    def connection_state(self):
        result=self.skyvern.public()
        if result.get("checkpoint"):
            with self.lock:
                result["checkpoint"]["artifact_id"]=self.artifact_id(result["checkpoint"].get("artifact") or "")
            result["checkpoint"].pop("artifact",None)
        return result

    def persona_state(self):
        return self.sanitize_personas(self.personas.snapshot())

    def sanitize_personas(self,result):
        with self.lock:
            for session in result["sessions"]:
                session["artifact_id"]=self.artifact_id(session.pop("artifact", None) or "")
                for point in session.get("trace",[]):
                    point["artifact_id"]=self.artifact_id(point.pop("artifact",None) or "")
        return result

    def persona_report(self,run_id=None):
        return build_report(self.sanitize_personas(self.personas.report_source(run_id)))

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps({"mode": "local_demo", "projects": [
                {"id": "banchan", "name": "반찬로드", "url": "http://localhost:4173", "type": "demo", "description": "반찬 구독 서비스 · 첫 주문 여정"}],
                "runs": sorted(self.runs.values(), key=lambda r: r["created_at"], reverse=True)}))

    def start(self, variant="fixed", case="all", project_id="banchan"):
        if project_id != "banchan":
            raise ValueError("Unsupported project")
        with self.lock:
            if any(r["status"] == "running" for r in self.runs.values()):
                raise RuntimeError("A diagnostic is already running")
            job = uuid4().hex
            created = datetime.now(timezone.utc).isoformat()
            self.runs[job] = {"id": job, "project_id": project_id, "project_name": "반찬로드", "status": "running",
                "variant": variant, "case": case, "created_at": created, "policy": "scripted_fixture",
                "label": ("문제 버전" if variant == "problem" else "개선 버전"), "sessions": [],
                "progress": {"finished": 0, "total": 2}, "events": [], "live_sessions":[], "plan":[], "error": None}
        Thread(target=self.worker, args=(job, variant, case, created, project_id), daemon=True).start()
        return job

    def worker(self, job, variant, case, created, project_id):
        def on_event(event):
            with self.lock:
                record = self.runs[job]
                record["events"].append({"type": event["type"], "session_id": event["session_id"],
                    "at": event["occurred_at"], "step_id": event["payload"].get("step_id")})
                record["events"] = record["events"][-8:]
                if event["type"] == "plan.created":
                    record["plan"] = event["payload"]["tasks"]
                    record["progress"]["total"] = event["payload"]["session_count"]
                if event["type"] == "session.finished":
                    record["progress"]["finished"] += 1
                    record["live_sessions"].append({"id":event["session_id"], "status":event["payload"]["status"], "goal":event["payload"].get("goal")})
        try:
            with shop_server() as base:
                config = RunInput(run_id="console_"+variant+"_"+case,
                    target_url=f"{base}/?variant={variant}&case={case}", allowed_origins=[base],
                    goal="반찬 식탁을 선택하고 합성 배송 정보로 모의 주문 완료", success_path="/order/success",
                    max_steps=35, personas=[Persona(id="commuter"), Persona(id="careful", context="주문 정보를 꼼꼼히 확인하는 첫 방문 사용자")])
                result = asyncio.run(execute_run(config, ShopScriptedPolicy,
                    output_dir=str(self.artifacts / "console-runs"), emit_event=on_event))
            path = Path(result["output_dir"]) / "summary.json"
            metadata = {"variant": variant, "case": case, "created_at": created, "job_id": job, "project_id":project_id}
            (path.parent / "console.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.lock:
                run = self.public_run(result, path, metadata)
                run["id"] = job
                run["evidence_id"] = result["attempt_id"]
                self.runs[job] = run
        except Exception as exc:
            with self.lock:
                self.runs[job].update(status="failed", error=f"실행 실패 ({type(exc).__name__}). 실행 환경을 확인해주세요.")


def make_server(port=4174, artifacts=None):
    store = ConsoleStore(artifacts)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, data, content_type="application/json; charset=utf-8", download=None):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/personas/reports":
                return self.respond(200,{"reports":store.personas.report_history()})
            if path in {"/api/personas/report","/api/personas/report.md","/api/personas/report.pdf"}:
                try:
                    query=parse_qs(urlsplit(self.path).query)
                    if set(query)-{'run_id'} or any(len(v)!=1 for v in query.values()):raise ValueError('리포트 실행 ID를 확인해주세요.')
                    report=store.persona_report(query.get('run_id',[None])[0])
                    if path.endswith('.pdf'):
                        if not store.pdf_slots.acquire(blocking=False):
                            return self.respond(429,{'error':'다른 PDF를 생성 중입니다. 잠시 후 다시 시도해주세요.'})
                        try:
                            data=asyncio.run(render_pdf(f'http://127.0.0.1:{self.server.server_port}',report['run_id']))
                            return self.respond(200,data,'application/pdf',download='dropspot-report-'+report['run_id']+'.pdf')
                        except Exception:
                            return self.respond(503,{'error':'PDF를 생성하지 못했습니다. Chromium 설치와 리포트 화면을 확인해주세요.'})
                        finally:
                            store.pdf_slots.release()
                    if path.endswith('.md'):
                        text=markdown_report(report,f'http://localhost:{self.server.server_port}')
                        return self.respond(200,text.encode('utf-8'),'text/markdown; charset=utf-8')
                    return self.respond(200,report)
                except FileNotFoundError:return self.respond(404,{"error":"저장된 실행을 찾을 수 없습니다."})
                except (ValueError,TypeError):return self.respond(409,{"error":"완료된 실행을 선택해주세요. 실행 중에는 리포트를 생성하지 않습니다."})
            if path == "/api/personas":
                return self.respond(200, store.persona_state())
            if path == "/api/connections/demo":
                return self.respond(200, store.connection_state())
            if path == "/api/state":
                return self.respond(200, store.snapshot())
            if path.startswith("/api/artifacts/"):
                token = path.rsplit("/", 1)[-1]
                with store.lock:
                    source = store.files.get(token)
                if source and source.is_file():
                    return self.respond(200, source.read_bytes(), "image/png")
                return self.respond(404, {"error": "근거 파일을 찾을 수 없습니다."})
            if path in {"/assets/NotoSansKR.woff2", "/assets/OFL-NotoSansKR.txt"}:
                source = FRONTEND_ROOT / "demo-shop" / path.lstrip("/")
            else:
                source = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
                if not source.is_relative_to(WEB.resolve()):
                    return self.respond(404, {"error": "Not found"})
            if not source.is_file():
                return self.respond(404, {"error": "Not found"})
            types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png":"image/png", ".woff2": "font/woff2", ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8"}
            return self.respond(200, source.read_bytes(), types.get(source.suffix, "application/octet-stream"))

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
                return self.respond(403, {"error": "다른 사이트에서 실행을 요청할 수 없습니다."})
            if urlsplit(self.path).path in {"/api/personas/discover","/api/personas/confirm","/api/personas/explain"}:
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=4096:raise ValueError('입력 크기를 확인해주세요.')
                    body=json.loads(self.rfile.read(size))
                    path=urlsplit(self.path).path
                    if path.endswith('/discover'):
                        if not isinstance(body,dict) or set(body)!={'variant'}:raise ValueError('반찬 데모 버전만 선택해주세요.')
                        return self.respond(200,store.planner.discover(body['variant']))
                    if path.endswith('/confirm'):
                        variant,tasks,audience=store.planner.confirm(body)
                        store.personas.start(variant,task_id='all',task_ids=tasks,target=audience)
                        return self.respond(202,store.persona_state())
                    if not isinstance(body,dict) or set(body) not in ({'run_id','session_id','question'},{'run_id','session_id','question','step'}):raise ValueError('질문과 세션을 확인해주세요.')
                    state=store.persona_state()
                    if body['run_id']!=state.get('id'):raise ValueError('실행이 바뀌었습니다. 현재 세션에서 다시 질문해주세요.')
                    answer=explain_session(state,body['session_id'],body['question'],body.get('step'))
                    return self.respond(200,store.personas.record_answer(body['run_id'],body['session_id'],answer))
                except (ValueError,TypeError) as exc:return self.respond(400,{'error':str(exc) or '입력 항목을 확인해주세요.'})
                except RuntimeError as exc:return self.respond(409,{'error':str(exc)})
                except Exception as exc:return self.respond(503,{'error':f'화면 탐색을 완료하지 못했습니다 ({type(exc).__name__}). 다시 시도해주세요.'})
            if urlsplit(self.path).path == "/api/personas/human":
                try:
                    size=int(self.headers.get("Content-Length","0"))
                    if not 0 < size <= 4096:raise ValueError('입력 크기를 확인해주세요.')
                    body=json.loads(self.rfile.read(size))
                    store.personas.record_human(body)
                    return self.respond(200,store.persona_state())
                except (ValueError,TypeError) as exc:
                    return self.respond(400,{"error":str(exc) or "입력 항목을 확인해주세요."})
            if urlsplit(self.path).path == "/api/personas/start":
                try:
                    size=int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 128:raise ValueError()
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict) or set(body) not in ({"variant"},{"variant","task_id"}):raise ValueError()
                    store.personas.start(body["variant"],body.get("task_id","order"))
                    return self.respond(202,store.persona_state())
                except (ValueError,TypeError):return self.respond(400,{"error":"반찬 데모의 fixed 또는 problem 버전만 실행할 수 있습니다."})
                except RuntimeError as exc:return self.respond(409,{"error":str(exc)})
            if urlsplit(self.path).path in {"/api/connections/demo/start","/api/connections/demo/checkpoint","/api/connections/demo/close"}:
                try:
                    action=urlsplit(self.path).path.rsplit('/',1)[-1]
                    result={"start":store.skyvern.start,"checkpoint":store.skyvern.checkpoint,"close":store.skyvern.close}[action]()
                    return self.respond(200,store.connection_state())
                except SkyvernUnavailable as exc:
                    return self.respond(503,{"error":str(exc)})
                except ValueError as exc:
                    return self.respond(409,{"error":str(exc)})
                except Exception as exc:
                    return self.respond(500,{"error":f"브라우저 연결 오류 ({type(exc).__name__})"})
            if urlsplit(self.path).path != "/api/runs":
                return self.respond(404, {"error": "Not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                project_id = body.get("project_id", "banchan")
                variant, case = body.get("variant"), body.get("case")
                if project_id != "banchan" or variant not in {"fixed", "problem"} or case not in {"all", "signup", "fee", "cta"}:
                    raise ValueError()
                job = store.start(variant, case)
                return self.respond(202, {"run_id": job})
            except RuntimeError:
                return self.respond(409, {"error": "진행 중인 진단이 있습니다. 완료 후 다시 실행해주세요."})
            except (ValueError, AttributeError, TypeError):
                return self.respond(400, {"error": "등록된 프로젝트와 허용된 실행 설정을 선택해주세요."})
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.store = store
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4174)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"DropSpot SaaS: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
