"""Print the existing report with local assets, without external network access."""
from urllib.parse import urlencode, urlsplit
from playwright.async_api import async_playwright


async def render_pdf(base_url, run_id):
    origin = urlsplit(base_url)
    if origin.scheme != 'http' or origin.hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('PDF rendering requires the local report server')
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1120, 'height': 900})
            async def local_only(route):
                target = urlsplit(route.request.url)
                if (target.scheme, target.netloc) == (origin.scheme, origin.netloc) and route.request.method == 'GET':
                    await route.continue_()
                else:
                    await route.abort()
            await page.route('**/*', local_only)
            await page.goto(base_url + '/report.html?' + urlencode({'run_id': run_id}), wait_until='networkidle', timeout=30000)
            await page.locator('#report:not([hidden])').wait_for(timeout=15000)
            await page.evaluate('''async () => {
                await document.fonts.ready;
                await Promise.all([...document.images].map(img => img.complete ? Promise.resolve() : new Promise(resolve => {img.onload=resolve;img.onerror=resolve;})));
                document.querySelectorAll('#report details').forEach(el => el.open=true);
            }''')
            await page.emulate_media(media='print')
            return await page.pdf(format='A4', print_background=True, prefer_css_page_size=True,
                                  display_header_footer=True, header_template='<span></span>',
                                  footer_template='<div style="font-size:9px;width:100%;text-align:center;color:#777">DropSpot · <span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        finally:
            await browser.close()
