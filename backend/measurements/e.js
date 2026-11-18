/* Exitplain 실측 수집 스크립트 (R01). 사이트 <head> 에
 *   <script src="https://<exitplain>/api/v1/e.js" data-project="ep_..." async></script>
 * 를 넣으면 화면 이동마다 {key, sid, path, step?, w(화면 너비)} 만 보낸다.
 *
 * 보내지 않는 것: 입력값, 쿠키, 쿼리 문자열, 화면 글자, 마우스 위치. sid 는 탭마다 새로 만드는 랜덤 값(sessionStorage).
 * 자동화 브라우저(navigator.webdriver, 우리 에이전트 포함)는 수집하지 않는다. 실측에 모의 방문이 섞이지 않게.
 * 경로로 나눌 수 없는 단계는 window.exitplain.step("coupon") 으로 이름을 붙일 수 있다.
 */
(function () {
  var me = document.currentScript;
  if (!me || window.__exitplainMeasure) return;
  window.__exitplainMeasure = true;
  var key = me.getAttribute("data-project") || "";
  if (!/^ep_[0-9a-f]{12}$/.test(key) || navigator.webdriver) return;
  var endpoint = new URL("/api/v1/collect", me.src).href;
  var SID = "exitplain:sid";
  var sid;
  try {
    sid = sessionStorage.getItem(SID);
    if (!sid) {
      sid = Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem(SID, sid);
    }
  } catch (e) {
    sid = Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  // 해시 라우터(#/cart)면 해시 경로를 쓴다. 쿼리 문자열은 개인정보가 섞일 수 있어 보내지 않는다.
  function currentPath() {
    var h = location.hash;
    if (h.indexOf("#/") === 0) return h.slice(1).split("?")[0];
    return location.pathname;
  }

  function send(body) {
    var data = JSON.stringify(body);
    // text/plain 이라 사전 요청(preflight) 없이 나간다
    if (navigator.sendBeacon && navigator.sendBeacon(endpoint, new Blob([data], { type: "text/plain" }))) return;
    try {
      fetch(endpoint, { method: "POST", body: data, keepalive: true, mode: "no-cors", headers: { "Content-Type": "text/plain" } });
    } catch (e) {
      /* 수집 실패는 사이트 동작에 영향을 주지 않는다 */
    }
  }

  var last = null;
  function view() {
    var path = currentPath();
    if (path === last) return;
    last = path;
    send({ key: key, sid: sid, path: path, w: window.innerWidth });
  }

  var push = history.pushState;
  history.pushState = function () {
    var r = push.apply(this, arguments);
    setTimeout(view, 0);
    return r;
  };
  var replace = history.replaceState;
  history.replaceState = function () {
    var r = replace.apply(this, arguments);
    setTimeout(view, 0);
    return r;
  };
  window.addEventListener("popstate", view);
  window.addEventListener("hashchange", view);

  window.exitplain = window.exitplain || {};
  window.exitplain.step = function (name) {
    if (typeof name === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(name)) send({ key: key, sid: sid, path: currentPath(), step: name, w: window.innerWidth });
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", view);
  else view();
})();
