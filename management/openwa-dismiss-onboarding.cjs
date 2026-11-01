const fs = require('node:fs');
const puppeteer = require('puppeteer-core');

(async () => {
  const [port] = fs.readFileSync('/app/chrome-profile/_IGNORE_default/DevToolsActivePort', 'utf8').trim().split('\n');
  const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${port}` });
  try {
    const page = (await browser.pages()).find((candidate) => candidate.url().startsWith('https://web.whatsapp.com/'));
    if (!page) throw new Error('WhatsApp page unavailable');
    const result = await page.evaluate(() => {
      const dialog = Array.from(document.querySelectorAll('[role="dialog"]')).find((element) =>
        element.textContent?.includes('new on WhatsApp Web') &&
        getComputedStyle(element).display !== 'none' &&
        getComputedStyle(element).visibility !== 'hidden'
      );
      if (!dialog) return 'dialog_absent';
      const button = Array.from(dialog.querySelectorAll('button,[role="button"]')).find((element) =>
        element.textContent?.trim() === 'Continue'
      );
      if (!button) return 'continue_button_absent';
      button.click();
      return 'continue_clicked';
    });
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const remaining = await page.evaluate(() => Array.from(document.querySelectorAll('[role="dialog"]'))
      .some((element) => element.textContent?.includes('new on WhatsApp Web') &&
        getComputedStyle(element).display !== 'none' && getComputedStyle(element).visibility !== 'hidden'));
    console.log(JSON.stringify({ result, dialogStillVisible: remaining }));
  } finally {
    browser.disconnect();
  }
})().catch((error) => { console.error(error.message); process.exit(1); });
