#!/usr/bin/env node
// screen-capture.mjs — takes the pictures for screen-capture.py. Nothing else calls it.
//
//   node screen-capture.mjs probe <playwright package dir>       -> JSON on stdout
//   node screen-capture.mjs capture <job.json>                    -> writes job.result
//
// It uses a Playwright install that already exists and a browser already in
// the Playwright cache. It never downloads anything.
//
// Read-only by construction, as far as a browser allows: a fresh context with
// no stored state; every request that is not GET, HEAD or OPTIONS is aborted;
// every request to another origin is aborted; service workers are blocked;
// every WebSocket is closed; popups are closed; dialogs are dismissed. Nothing
// is clicked, typed or hovered. A GET that has side effects on the server
// still runs: that limit is in docs/turns.md.

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const PHONES = ['iPhone 15', 'iPhone 14', 'iPhone 13'];

function load(pkgDir) {
  const require = createRequire(path.join(pkgDir, 'package.json'));
  return require(pkgDir);
}

function hasBrowser(pw, name) {
  try {
    const p = pw[name].executablePath();
    return Boolean(p) && fs.existsSync(p);
  } catch {
    return false;
  }
}

function probe(pkgDir) {
  const out = { pkg: pkgDir, chromium: false, webkit: false, version: null };
  try {
    out.version = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8')).version || null;
    const pw = load(pkgDir);
    out.chromium = hasBrowser(pw, 'chromium');
    out.webkit = hasBrowser(pw, 'webkit');
  } catch (e) {
    out.error = String(e && e.message || e).slice(0, 300);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
}

async function launch(pw, name) {
  try {
    return await pw[name].launch({ headless: true });
  } catch (e) {
    // Newer Playwright runs headless Chromium from a separate shell build. When
    // only the full browser is in the cache, its own headless mode still works.
    if (name === 'chromium') return await pw.chromium.launch({ headless: true, channel: 'chromium' });
    throw e;
  }
}

// The overlay a dev server draws over the page when the build is broken.
function overlayProbe() {
  const vite = document.querySelector('vite-error-overlay');
  if (vite) {
    const t = ((vite.shadowRoot && vite.shadowRoot.textContent) || vite.textContent || '').trim();
    return { kind: 'vite', text: t.slice(0, 400) };
  }
  for (const portal of document.querySelectorAll('nextjs-portal')) {
    const root = portal.shadowRoot;
    if (!root) continue;
    const dlg = root.querySelector(
      '[data-nextjs-dialog], [data-nextjs-dialog-overlay], #nextjs__container_errors_label, '
      + '#nextjs__container_build_error_label, [data-nextjs-error-overlay]');
    if (dlg) return { kind: 'next', text: (dlg.textContent || '').trim().slice(0, 400) };
  }
  return null;
}

async function guardedContext(browser, options, allowed, counters) {
  const ctx = await browser.newContext({
    ...options,
    serviceWorkers: 'block',
    acceptDownloads: false,
    permissions: [],
  });
  await ctx.route('**/*', (route) => {
    const req = route.request();
    let origin = '';
    try { origin = new URL(req.url()).origin; } catch { /* not a URL: blocked below */ }
    if (!READ_METHODS.has(req.method())) {
      counters.blocked_method += 1;
      return route.abort('blockedbyclient');
    }
    if (!allowed.has(origin)) {
      counters.blocked_origin += 1;
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  if (typeof ctx.routeWebSocket === 'function') {
    await ctx.routeWebSocket(/.*/, (ws) => { counters.blocked_ws += 1; ws.close(); });
  } else {
    counters.ws_not_blocked = true; // a Playwright too old to route sockets
  }
  ctx.on('page', async (p) => {
    // A page opened by another page is a popup: close it. The capture's own page has no opener.
    if (await p.opener().catch(() => null)) p.close().catch(() => {});
  });
  return ctx;
}

async function shoot(ctx, url, file, maxHeight, allowed) {
  const page = await ctx.newPage();
  page.on('dialog', (d) => d.dismiss().catch(() => {}));
  page.on('popup', (p) => p.close().catch(() => {}));
  const serverErrors = [];
  page.on('response', (r) => {
    try {
      const u = new URL(r.url());
      const t = r.request().resourceType();
      if (r.status() >= 500 && allowed.has(u.origin) && ['document', 'script', 'stylesheet'].includes(t)) {
        serverErrors.push(`${r.status()} ${t} ${u.pathname}`);
      }
    } catch { /* ignore */ }
  });
  const shot = { file, http_status: null, build_error: null, capped: false, ok: false };
  try {
    const resp = await page.goto(url, { waitUntil: 'load', timeout: 20000 });
    shot.http_status = resp ? resp.status() : null;
    await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
    await page.evaluate(() => (document.fonts ? document.fonts.ready.then(() => true) : true)).catch(() => {});
    await page.waitForTimeout(300);
    shot.build_error = await page.evaluate(overlayProbe).catch(() => null);
    if (!shot.build_error && serverErrors.length) {
      shot.build_error = { kind: 'http', text: serverErrors.slice(0, 5).join('; ') };
    }
    const height = await page.evaluate(() => Math.max(
      document.documentElement ? document.documentElement.scrollHeight : 0,
      document.body ? document.body.scrollHeight : 0)).catch(() => 0);
    const vp = page.viewportSize();
    const opts = { path: file, fullPage: true, animations: 'disabled', caret: 'hide' };
    if (height > maxHeight && vp) {
      opts.clip = { x: 0, y: 0, width: vp.width, height: maxHeight };
      shot.capped = true;
    }
    await page.screenshot(opts);
    shot.ok = true;
  } catch (e) {
    shot.error = String(e && e.message || e).split('\n')[0].slice(0, 300);
  } finally {
    await page.close().catch(() => {});
  }
  return shot;
}

async function capture(jobPath) {
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  const resultPath = jobPath + '.result';
  const result = { shots: [], counters: null, error: null, engines: {} };
  const save = () => fs.writeFileSync(resultPath, JSON.stringify(result));
  const timer = setTimeout(() => { result.error = 'deadline'; save(); process.exit(3); }, job.deadline_ms || 40000);
  const counters = { blocked_method: 0, blocked_origin: 0, blocked_ws: 0 };
  result.counters = counters;
  const allowed = new Set(job.origins);
  const browsers = [];
  try {
    const pw = load(job.pkg);
    let chromium;
    try {
      chromium = await launch(pw, 'chromium');
      browsers.push(chromium);
    } catch (e) {
      result.error = 'no_browser: ' + String(e && e.message || e).split('\n')[0].slice(0, 300);
      save();
      return;
    }
    // Phone: WebKit with an iPhone profile when WebKit is installed, else Chromium at the phone size.
    const device = PHONES.map((n) => pw.devices[n]).find(Boolean) || null;
    let phoneBrowser = chromium;
    let phoneEngine = 'chromium';
    if (job.webkit) {
      try {
        phoneBrowser = await launch(pw, 'webkit');
        browsers.push(phoneBrowser);
        phoneEngine = 'webkit';
      } catch { /* fall back to Chromium */ }
    }
    const phoneOpts = device
      ? (({ defaultBrowserType, ...rest }) => rest)(device)
      : { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true };
    result.engines = { desktop: 'chromium', phone: phoneEngine, phone_profile: device ? PHONES.find((n) => pw.devices[n]) : null };
    for (const item of job.items) {
      for (const want of item.shots) {
        const isPhone = want.viewport === 'phone';
        const browser = isPhone ? phoneBrowser : chromium;
        const opts = isPhone ? phoneOpts : { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };
        const ctx = await guardedContext(browser, opts, allowed, counters);
        const shot = await shoot(ctx, item.url, want.file, job.max_height || 6000, allowed);
        await ctx.close().catch(() => {});
        const vp = opts.viewport;
        result.shots.push({
          ...shot, route: item.route, url: item.url, viewport_name: want.viewport,
          viewport: `${vp.width}x${vp.height}`, engine: isPhone ? phoneEngine : 'chromium',
        });
        save();
      }
    }
  } catch (e) {
    result.error = String(e && e.message || e).split('\n')[0].slice(0, 300);
  } finally {
    for (const b of browsers) await b.close().catch(() => {});
    clearTimeout(timer);
    save();
  }
}

const [mode, arg] = process.argv.slice(2);
if (mode === 'probe' && arg) {
  probe(arg);
} else if (mode === 'capture' && arg) {
  capture(arg).then(() => process.exit(0), (e) => {
    process.stderr.write(String(e) + '\n');
    process.exit(1);
  });
} else {
  process.stderr.write('usage: screen-capture.mjs probe <pkg-dir> | capture <job.json>\n');
  process.exit(2);
}
