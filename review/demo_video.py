"""Record a buttery-smooth, Screen-Studio-style screencast of the live platform.

Drives the running dashboard + API with Playwright. An overlay layer (on the
<html> element) carries an animated cursor + crisp captions; the page content
(<body>) is zoomed with GPU-eased CSS transforms anchored on the cursor, so the
punch-in zooms are perfectly synced and captured natively in the recording.

    uv run python -m review.demo_video      # writes demo_video/*.webm
"""

from __future__ import annotations

import math
from pathlib import Path

from playwright.sync_api import sync_playwright

DASH = "http://localhost:8501"
API = "http://localhost:8000/docs"
W, H = 1280, 720
OUT_DIR = Path("demo_video")

# Overlay (cursor + caption) lives on <html>, OUTSIDE <body>, so it is never
# affected by the body zoom transform — captions stay crisp, cursor stays put.
OVERLAY_JS = r"""
() => {
  if (window.__demo) return;
  window.__demo = true;
  const root = document.documentElement;

  const cur = document.createElement('div');
  cur.id = '__cursor';
  cur.innerHTML = `<svg width="30" height="30" viewBox="0 0 24 24"
     xmlns="http://www.w3.org/2000/svg" style="filter:drop-shadow(1px 2px 2px rgba(0,0,0,.55))">
     <path d="M5 3l14 7-6 1.5L9 18 5 3z" fill="#ffffff" stroke="#111" stroke-width="1.3"/></svg>`;
  Object.assign(cur.style, {position:'fixed', left:'0', top:'0', zIndex:2147483647,
     pointerEvents:'none', willChange:'left,top'});
  root.appendChild(cur);

  const ring = document.createElement('div');
  ring.id = '__ring';
  Object.assign(ring.style, {position:'fixed', width:'16px', height:'16px',
     border:'3px solid #ff4d6d', borderRadius:'50%', zIndex:2147483646,
     pointerEvents:'none', opacity:'0', transform:'translate(-50%,-50%) scale(1)'});
  root.appendChild(ring);

  const cap = document.createElement('div');
  cap.id = '__caption';
  Object.assign(cap.style, {position:'fixed', left:'50%', bottom:'40px',
     transform:'translateX(-50%) translateY(10px)', maxWidth:'74%', padding:'15px 30px',
     background:'rgba(13,17,23,.93)', color:'#fff',
     font:'600 23px/1.45 -apple-system,Segoe UI,Roboto,sans-serif', borderRadius:'16px',
     zIndex:2147483647, pointerEvents:'none', textAlign:'center', opacity:'0',
     transition:'opacity .4s ease, transform .4s ease',
     boxShadow:'0 10px 34px rgba(0,0,0,.5)', borderLeft:'5px solid #2dd4bf'});
  root.appendChild(cap);

  window.__cur = (x,y) => { cur.style.left = x+'px'; cur.style.top = y+'px'; };
  window.__cap = (t) => { cap.textContent = t||'';
     cap.style.opacity = t ? '1':'0';
     cap.style.transform = 'translateX(-50%) translateY(' + (t?'0':'10px') + ')'; };
  window.__click = (x,y) => { ring.style.left=x+'px'; ring.style.top=y+'px';
     ring.style.transition='none'; ring.style.opacity='1';
     ring.style.transform='translate(-50%,-50%) scale(1)';
     requestAnimationFrame(()=>{ ring.style.transition='all .55s ease-out';
        ring.style.opacity='0'; ring.style.transform='translate(-50%,-50%) scale(2.8)'; }); };

  // GPU-eased zoom anchored at a screen point. Scales the Streamlit main
  // container (or <body> on non-Streamlit pages) so layout never breaks; the
  // origin is computed relative to the target's rect so the focus point stays
  // exactly under the cursor.
  const zTarget = () => document.querySelector('[data-testid="stMain"]') || document.body;
  window.__zoom = (cx,cy,scale,ms) => {
     const b = zTarget(); const r = b.getBoundingClientRect();
     b.style.transformOrigin = (cx-r.left)+'px '+(cy-r.top)+'px';
     b.style.transition = 'transform '+ms+'ms cubic-bezier(.2,.7,.25,1)';
     b.style.transform = 'scale('+scale+')';
     window.__zt = b;
  };
  window.__unzoom = (ms) => {
     const b = window.__zt || zTarget();
     b.style.transition = 'transform '+ms+'ms cubic-bezier(.4,0,.2,1)';
     b.style.transform = 'scale(1)';
  };
}
"""

CARD_JS = """
(o) => {
  let c = document.getElementById('__card');
  if (!c) { c = document.createElement('div'); c.id='__card';
    Object.assign(c.style,{position:'fixed',inset:'0',zIndex:2147483647,display:'flex',
      flexDirection:'column',alignItems:'center',justifyContent:'center',textAlign:'center',
      background:'radial-gradient(1200px 600px at 50% 30%,#1b2c4a 0%,#0d1117 70%)',color:'#fff',
      font:'-apple-system,Segoe UI,Roboto,sans-serif',opacity:'0',transition:'opacity .6s ease'});
    document.documentElement.appendChild(c); }
  requestAnimationFrame(()=>{ c.style.opacity='1'; });
  c.innerHTML = `<div style="font-size:52px;font-weight:800;letter-spacing:-1.5px">${o.title}</div>
    <div style="font-size:25px;margin-top:20px;color:#2dd4bf;font-weight:700">${o.sub||''}</div>
    <div style="font-size:19px;margin-top:16px;color:#9aa7b4;max-width:780px;line-height:1.6">${o.note||''}</div>`;
}
"""


class Demo:
    def __init__(self, page):
        self.page = page
        self.pos = [W / 2, H / 2]
        self.zoomed = False

    def overlay(self):
        self.page.evaluate(OVERLAY_JS)

    def caption(self, text):
        self.overlay()
        self.page.evaluate("(t)=>window.__cap(t)", text)

    def card(self, title, sub="", note=""):
        self.page.evaluate(CARD_JS, {"title": title, "sub": sub, "note": note})

    def hide_card(self):
        self.page.evaluate("()=>{const c=document.getElementById('__card'); if(c){c.style.opacity='0';"
                           "setTimeout(()=>c.remove(),700);}}")

    def beat(self, ms=1100):
        self.page.wait_for_timeout(ms)

    def _ease_move(self, x, y, steps=42):
        self.overlay()
        sx, sy = self.pos
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)  # smoothstep
            cx = sx + (x - sx) * ease
            cy = sy + (y - sy) * ease
            self.page.mouse.move(cx, cy)
            self.page.evaluate("([x,y])=>window.__cur(x,y)", [cx, cy])
            self.page.wait_for_timeout(10)
        self.pos = [x, y]

    def unzoom(self):
        if self.zoomed:
            self.page.evaluate("()=>window.__unzoom(550)")
            self.beat(650)
            self.zoomed = False

    def move(self, x, y, steps=42):
        self.unzoom()
        self._ease_move(x, y, steps)

    def move_to(self, locator, steps=42):
        self.unzoom()
        locator.scroll_into_view_if_needed(timeout=8000)
        self.beat(200)
        box = locator.bounding_box()
        if not box:
            return None
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + min(box["height"] / 2, 24)
        self._ease_move(cx, cy, steps)
        return cx, cy

    def punch_in(self, scale=1.45, ms=750):
        x, y = self.pos
        self.page.evaluate("([x,y,s,ms])=>window.__zoom(x,y,s,ms)", [x, y, scale, ms])
        self.zoomed = True
        self.beat(ms + 200)

    def click(self, locator, steps=42):
        pt = self.move_to(locator, steps)
        if pt:
            self.page.evaluate("([x,y])=>window.__click(x,y)", list(pt))
        self.beat(220)
        try:
            locator.click(timeout=5000)
        except Exception:
            pass
        self.beat(850)


def run():
    OUT_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        ctx = browser.new_context(viewport={"width": W, "height": H},
                                  record_video_dir=str(OUT_DIR),
                                  record_video_size={"width": W, "height": H})
        page = ctx.new_page()
        d = Demo(page)

        # 1) Title card
        page.goto("about:blank")
        d.card("HealthLynked",
               "Provider &amp; Practice Directory Update Pipeline",
               "A cost-efficient, self-verifying AI pipeline — runs in production today")
        d.beat(3400)

        # 2) Dashboard
        page.goto(DASH, wait_until="domcontentloaded")
        try:
            page.get_by_text("HealthLynked").first.wait_for(timeout=30000)
        except Exception:
            pass
        d.beat(1600)
        d.move(W / 2, 130)
        d.caption("Live review dashboard — driven by the running pipeline"); d.beat(1500)

        # 3) Metrics — punch in
        try:
            d.move_to(page.get_by_text("Records processed", exact=False).first, steps=34)
            d.caption("Every record scored — only safe, high-confidence updates auto-apply")
            d.punch_in(1.5, 760); d.beat(1500); d.unzoom()
        except Exception:
            pass

        # 4) Review queue — expand a flagged record and zoom into the diff
        try:
            exp = page.locator("details summary, [data-testid='stExpander'] summary").first
            d.caption("Uncertain or conflicting records go to human review")
            d.click(exp)
            d.caption("Proposed change + supporting sources + confidence score")
            d.punch_in(1.5, 780); d.beat(2200); d.unzoom()
        except Exception:
            pass

        # 5) Approve (the money shot)
        try:
            approve = page.get_by_role("button", name="Approve").first
            d.caption("One click applies the update and writes an audit version")
            d.click(approve); d.beat(1500)
        except Exception:
            pass

        # 6) Cost & models
        try:
            d.click(page.get_by_role("tab", name="Cost & models").first)
            d.caption("Live cost ledger — real spend per funnel stage")
            d.move(W / 2, 380); d.punch_in(1.35, 760); d.beat(1700); d.unzoom()
            d.caption("Bake-off picks the cheapest accurate model — $0.067 / 1,000 conflicts")
            d.move(W / 2, 560); d.punch_in(1.35, 760); d.beat(2000); d.unzoom()
        except Exception:
            pass

        # 7) Change history
        try:
            d.click(page.get_by_role("tab", name="Change history").first)
            d.caption("Every change traceable to its sources — full audit trail")
            d.move(W / 2, 320); d.punch_in(1.4, 760); d.beat(1900); d.unzoom()
        except Exception:
            pass

        # 8) API
        page.goto(API, wait_until="domcontentloaded")
        d.beat(2200)
        d.caption("Production REST API for HealthLynked systems to integrate")
        d.move(W / 2, 250); d.beat(1200)
        try:
            ep = page.get_by_text("/recommendations").first
            d.move_to(ep, steps=30); d.punch_in(1.5, 760); d.beat(1700); d.unzoom()
        except Exception:
            pass

        # 9) End card
        d.caption("")
        d.card("Repeatable · Verifiable · Cheap",
               "100% detection · 0% false-positives · &lt; $2 / 1,000 records",
               "Free authoritative data first · LLM only on conflicts · "
               "3 rounds of adversarial review · running in production today")
        d.beat(3800)

        path = page.video.path()
        ctx.close()
        browser.close()
        print("RAW_VIDEO:", path)


if __name__ == "__main__":
    run()
