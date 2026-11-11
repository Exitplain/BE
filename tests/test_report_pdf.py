import unicodedata
from io import BytesIO
from pypdf import PdfReader
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen
import pytest
from playwright.async_api import async_playwright, expect
from agent.console import make_server
from test_persona_report import report_state


def test_pdf_completed_report_and_busy_incomplete_guards(tmp_path):
    server=make_server(0,tmp_path);server.store.personas.state=report_state(tmp_path)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/api/personas/report.pdf'
    try:
        with urlopen(url+'?run_id=report_fixture',timeout=45) as response:
            data=response.read()
            assert response.headers['Content-Type']=='application/pdf'
            assert 'dropspot-report-report_fixture.pdf' in response.headers['Content-Disposition']
            assert data.startswith(b'%PDF-') and len(data)>10000
            pdf=PdfReader(BytesIO(data));text=unicodedata.normalize('NFKC',''.join(page.extract_text() for page in pdf.pages))
            assert '회원가입에서 필수 입력 부담' in text
            assert 'report_fixture' in text and '실행 오류는 따로 확인하세요' in text
            assert 'Human Alignment' in text and '미검증' in text
            assert abs(float(pdf.pages[0].mediabox.width)-595.28)<2
        server.store.pdf_slots.acquire()
        try:
            with pytest.raises(HTTPError) as busy:urlopen(url)
            assert busy.value.code==429
        finally:server.store.pdf_slots.release()
        for query in ['?run_id=..%2Fprivate','?run_id=missing']:
            with pytest.raises(HTTPError) as invalid:urlopen(url+query)
            assert invalid.value.code in {404,409}
        server.store.personas.state.update(status='running')
        with pytest.raises(HTTPError) as running:urlopen(url)
        assert running.value.code==409
    finally:server.shutdown();server.server_close();thread.join()


async def test_report_pdf_button_downloads_selected_report(tmp_path):
    server=make_server(0,tmp_path);server.store.personas.state=report_state(tmp_path)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page()
            try:
                await page.goto(f'http://127.0.0.1:{server.server_port}/#report/report_fixture')
                f=page.frame_locator('iframe[title="진단 리포트"]')
                button=f.get_by_role('button',name='PDF 내보내기',exact=True)
                await expect(button).to_be_visible()
                async with page.expect_download(timeout=45000) as pending:await button.click()
                download=await pending.value
                assert download.suggested_filename=='dropspot-report-report_fixture.pdf'
                assert Path(await download.path()).read_bytes().startswith(b'%PDF-')
                await expect(button).to_be_enabled()
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()
