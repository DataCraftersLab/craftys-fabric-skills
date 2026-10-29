"""
The standing checks, run in a real browser against real output.

    python tests/semantic-model-analyzer/validate_html.py            # render the fixtures, then check
    python tests/semantic-model-analyzer/validate_html.py <dir>      # check pages already written

Separate from `test_pipeline.py` because it needs Playwright, which the bundle otherwise
does not, and because opening three pages in two themes is slow enough that it should be
a deliberate act rather than part of every run:

    pip install playwright && playwright install chromium

Each check exists because the thing it checks for has actually happened here at least
once, and none of them were visible in the HTML source:

* a hardcoded hex survives a theme toggle and goes invisible in one of the two
* a CDN reference ends the one property that makes these files worth sending
* prose CSS out-specified a widget rule and flexbox sliced two labels mid-letter
* a chevron got drawn twice, once by the theme and once by me, at different angles
* a label ran off the right edge of its own chart and nothing said so
"""

import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "semantic-model-analyzer" / "skills" / "semantic-model-analyzer"))

#: The type scale the DC guideline allows inside a chart. Anything else is a call site
#: that opted out of the scale, usually by passing no size at all.
ALLOWED_SVG_PX = {10.0, 12.0, 20.0}

#: A text node may sit this far outside its own SVG before it counts as clipped. One
#: pixel of antialiasing is not a defect; six is a label running off the edge.
CLIP_SLACK_PX = 2.0


class Report:
    def __init__(self):
        self.fails = []
        self.checks = 0

    def check(self, ok, label, detail=""):
        self.checks += 1
        if not ok:
            self.fails.append((label, detail))
        print("  %s %s%s" % ("ok  " if ok else "FAIL", label,
                             "" if ok else "\n       " + str(detail)[:2000]))


PROBE = r"""() => {
  const out = {fontSizes: {}, rotated: [], clipped: [], anchors: [], invisible: [],
               overflowX: document.documentElement.scrollWidth - window.innerWidth};

  for (const t of document.querySelectorAll('svg text, svg tspan')) {
    if (!(t.textContent || '').trim()) continue;
    const cs = getComputedStyle(t);
    const px = Math.round(parseFloat(cs.fontSize) * 10) / 10;
    out.fontSizes[px] = (out.fontSizes[px] || 0) + 1;
    const tr = (t.getAttribute('transform') || '') + ' ' + (cs.transform || '');
    if (/rotate/i.test(tr) && !t.closest('[data-allow-rotate]'))
      out.rotated.push((t.textContent || '').trim().slice(0, 40));
  }

  for (const svg of document.querySelectorAll('svg')) {
    const vb = svg.viewBox && svg.viewBox.baseVal;
    if (!vb || !vb.width) continue;
    for (const t of svg.querySelectorAll('text')) {
      if (!(t.textContent || '').trim()) continue;
      let b; try { b = t.getBBox(); } catch (e) { continue; }
      const over = Math.max(vb.x - b.x, b.x + b.width - (vb.x + vb.width),
                            vb.y - b.y, b.y + b.height - (vb.y + vb.height));
      if (over > SLACK)
        out.clipped.push({text: (t.textContent || '').trim().slice(0, 40),
                          over: Math.round(over)});
    }
  }

  for (const a of document.querySelectorAll('a[href^="#"]')) {
    const id = decodeURIComponent(a.getAttribute('href').slice(1));
    if (id) out.anchors.push([id, !!document.getElementById(id)]);
  }

  const bg = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
    }
    return getComputedStyle(document.body).backgroundColor;
  };
  const rgb = (c) => (c.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
  for (const el of document.querySelectorAll('p, li, td, th, h1, h2, h3, h4, svg text')) {
    if (!(el.textContent || '').trim()) continue;
    const a = rgb(getComputedStyle(el).color), b = rgb(bg(el));
    if (a.length === 3 && b.length === 3 &&
        Math.abs(a[0]-b[0]) + Math.abs(a[1]-b[1]) + Math.abs(a[2]-b[2]) < 24)
      out.invisible.push((el.textContent || '').trim().slice(0, 40));
  }
  return out;
}""".replace("SLACK", str(CLIP_SLACK_PX))


def check_page(page, path, rep, theme):
    errors, requests = [], []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))

    page.goto(path.as_uri(), wait_until="load")
    if theme == "dark":
        page.click("[data-dc-theme]")
        page.wait_for_timeout(150)

    tag = "%s [%s]" % (path.name, theme)
    # One view at a time hides the rest from every check below, so the probe runs over
    # the whole document, as "Show all" and print both show it.
    if page.query_selector("[data-view-all]"):
        page.click("[data-view-all]")
        page.wait_for_timeout(150)
    app = page.evaluate("""() => {
      let ix = null;
      try { ix = JSON.parse(document.getElementById('sma-index').textContent); } catch (e) {}
      const views = [...document.querySelectorAll('.sma-view')].map(v => v.dataset.view);
      const tabs = [...document.querySelectorAll('.sma-tab[data-view]')].map(t => t.dataset.view);
      return {ix: !!ix, objects: ix ? ix.objects.length : 0,
              viewsTabbed: views.every(v => tabs.includes(v)), finder: !!document.getElementById('sma-find')};
    }""")
    rep.check(app["ix"] and app["finder"], "%s  finder and its index load" % tag, app)
    rep.check(app["viewsTabbed"], "%s  every view has a tab" % tag, app)
    r = page.evaluate(PROBE)

    rep.check(not errors, "%s  no console or page errors" % tag, errors[:5])
    external = [u for u in requests if not u.startswith("file:")]
    rep.check(not external, "%s  no network requests" % tag, external[:5])
    rep.check(r["overflowX"] <= 1, "%s  no horizontal page scroll" % tag,
              "body is %dpx wider than the viewport" % r["overflowX"])

    sizes = {float(k) for k in r["fontSizes"]}
    off = sorted(sizes - ALLOWED_SVG_PX)
    rep.check(not off, "%s  chart type scale is %s" % (
        tag, "/".join("%g" % s for s in sorted(ALLOWED_SVG_PX))),
        "off-scale sizes %s (counts %s)" % (off, r["fontSizes"]))
    rep.check(not r["rotated"], "%s  no rotated chart labels" % tag, r["rotated"][:6])
    rep.check(not r["clipped"], "%s  no text outside its own chart" % tag,
              r["clipped"][:6])
    rep.check(not r["invisible"], "%s  no text the same colour as its ground" % tag,
              r["invisible"][:6])

    broken = [a for a, ok in r["anchors"] if not ok]
    rep.check(not broken, "%s  every in-page anchor resolves" % tag, broken[:8])

    hrefs = set(re.findall(r'href="([a-z-]+\.html)', path.read_text(encoding="utf-8")))
    missing = sorted(h for h in hrefs if not (path.parent / h).exists())
    rep.check(not missing, "%s  no link to a page that was not written" % tag, missing)


def main(argv):
    from playwright.sync_api import sync_playwright

    tmp = None
    if argv:
        out = Path(argv[0])
    else:
        from sma import dataset, inputs
        from sma.render import build_pages
        tmp = out = Path(tempfile.mkdtemp())
        fix = Path(__file__).parent / "fixtures"
        build_pages(dataset.build(inputs.resolve([str(fix)])), out)
        print("rendered the fixtures into %s\n" % out)

    pages = sorted(out.glob("*.html"))
    if not pages:
        print("no .html in %s" % out)
        return 2

    rep = Report()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for theme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                          color_scheme=theme)
                page = ctx.new_page()
                for f in pages:
                    check_page(page, f, rep, theme)
                ctx.close()
            browser.close()
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d checks, %d failed" % (rep.checks, len(rep.fails)))
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
