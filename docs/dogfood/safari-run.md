# Real Safari run — what the owner has to do once, then a runnable script

**Local test instance, prototype, synthetic data.** Written 2026-10-17 by Hermes subagent W18.

**Status: NOT RUN.** Safari 27.0.1 (`/usr/bin/safaridriver`, "Included with Safari 27.0.1 (22625.1.29.11.28)") is installed on this Mac, but WebDriver control is switched off, and enabling it needs an admin password and a click in Safari that an agent must not do. The exact failure from a real attempt (Selenium 4.51.0, `webdriver.Safari()`):

```
SessionNotCreatedException
Message: Could not create a session: You must enable 'Allow remote automation' in the Developer section of Safari Settings to control Safari via WebDriver.
```

(`defaults read com.apple.Safari AllowRemoteAutomation` → "Domain 'com.apple.Safari' not found": Safari is sandboxed, so that key is not readable that way. Do not rely on it.)

Nothing below has been run against Safari. The script itself was checked end to end against Firefox 158 (same WebDriver calls, `BROWSER=firefox`); the Safari-specific part is only the `webdriver.Safari()` line.

## One-time setup (owner, about 1 minute)

1. Safari → Settings → **Advanced** → tick **Show features for web developers** (Safari 27 name; older: "Show Develop menu in menu bar").
2. Safari → Settings → **Developer** → tick **Allow remote automation**. (Older versions: menu bar **Develop → Allow Remote Automation**.)
3. In Terminal, once: `sudo safaridriver --enable` (asks for your admin password; do this yourself, not through an agent).
4. Quit Safari. The automation run opens its own Safari window with an orange "automation" bar; it uses a clean profile, so your logins and extensions are not involved.
5. Safari tests are **headed**: a Safari window opens and the Mac must stay awake and unlocked for the run (about 1 minute for the script below).

## Start the test instance

From the repo, with disposable data (never your real data home):

```sh
cd "<repo>/web"
export ATHENA_DATA_HOME=/tmp/athena-safari-data DJANGO_DEBUG=1
rm -rf $ATHENA_DATA_HOME && mkdir -p $ATHENA_DATA_HOME
python manage.py migrate
DJANGO_SUPERUSER_PASSWORD='<pick one, put it in the env only>' python manage.py createsuperuser --noinput --username tester --email t@example.test
python manage.py runserver 127.0.0.1:8817 --noreload
```

## Script (`safari_smoke.py`; save it anywhere, run in a second terminal)

```sh
python3 -m venv /tmp/sfvenv && /tmp/sfvenv/bin/pip install selenium
ATHENA_TEST_PASSWORD='<same password>' /tmp/sfvenv/bin/python safari_smoke.py          # Safari
ATHENA_TEST_PASSWORD='<same password>' BROWSER=firefox FIREFOX_BIN=/path/to/Firefox.app/Contents/MacOS/firefox GECKODRIVER=/path/to/geckodriver /tmp/sfvenv/bin/python safari_smoke.py   # harness self-check
```

```python
"""Safari/WebDriver smoke test for the Athena console. Local test instance, prototype, synthetic data.
Checks: login, console loads, no JS errors / CSP violations, no horizontal overflow, form field sizes,
focus ring on 25 Tab/Option+Tab stops, New project dialog (open, Tab trap, Esc, focus return), create project.
Prints one line per check and a JSON blob. Password comes from ATHENA_TEST_PASSWORD only."""
import json, os, sys, time
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE = os.environ.get("BASE", "http://127.0.0.1:8817")
PW = os.environ["ATHENA_TEST_PASSWORD"]
BROWSER = os.environ.get("BROWSER", "safari")
if BROWSER == "firefox":
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    o = Options(); o.add_argument("-headless"); o.binary_location = os.environ["FIREFOX_BIN"]
    d = webdriver.Firefox(options=o, service=Service(os.environ["GECKODRIVER"]))
else:
    d = webdriver.Safari()
R = {"browser": BROWSER, "version": d.capabilities.get("browserVersion"), "checks": []}

def ok(name, passed, detail=""):
    R["checks"].append({"name": name, "ok": bool(passed), "detail": detail}); print("PASS" if passed else "FAIL", name, detail)

def js(s, *a): return d.execute_script(s, *a)
def wait(cond, t=15):
    end = time.time() + t
    while time.time() < end:
        try:
            v = cond()
            if v: return v
        except Exception: pass
        time.sleep(0.15)
    raise TimeoutError

# Safari has no BiDi log: install listeners right after each load (misses errors thrown during the first parse).
LISTEN = """window.__e=window.__e||[]; if(!window.__l){window.__l=1;
 window.addEventListener('error',e=>window.__e.push('error: '+e.message));
 window.addEventListener('unhandledrejection',e=>window.__e.push('rejection: '+e.reason));
 document.addEventListener('securitypolicyviolation',e=>window.__e.push('CSP '+e.violatedDirective+' @'+(e.sourceFile||'')+':'+e.lineNumber));}"""
def tab(shift=False):
    ac = ActionChains(d)
    # Safari skips buttons/links on plain Tab unless Settings > Advanced > "Press Tab to highlight each item" is on; Option+Tab always reaches them.
    mods = [Keys.ALT] if BROWSER == "safari" else []
    for m in mods: ac.key_down(m)
    if shift: ac.key_down(Keys.SHIFT)
    ac.send_keys(Keys.TAB)
    if shift: ac.key_up(Keys.SHIFT)
    for m in mods: ac.key_up(m)
    ac.perform()
def sig(): return js("const a=document.activeElement;return !a?'none':a===document.body?'body':(a.id?'#'+a.id:a.tagName.toLowerCase()+':'+(a.getAttribute('aria-label')||a.textContent||'').trim().slice(0,20))")

try:
    d.set_window_size(1280, 900)
    d.get(BASE + "/login/"); wait(lambda: js("return document.readyState")=="complete"); js(LISTEN)
    d.find_element(By.ID, "id_username").send_keys("tester"); d.find_element(By.ID, "id_password").send_keys(PW)
    d.find_element(By.CSS_SELECTOR, "button[type=submit]").click(); wait(lambda: "/login" not in d.current_url)
    ok("sign in", True, d.current_url)
    d.get(BASE + "/"); wait(lambda: d.find_elements(By.CSS_SELECTOR, "#dataset-rows, #empty-create, #mobile-rows")); js(LISTEN); time.sleep(1.5)
    ok("console loads", True, d.title)
    ok("no horizontal overflow (current width)", js("const e=document.documentElement;return e.scrollWidth<=window.innerWidth+1"), str(js("return [document.documentElement.scrollWidth,innerWidth]")))
    small = js("""const out=[];for(const el of document.querySelectorAll('input,select,textarea')){if(el.tagName==='INPUT'&&['hidden','checkbox','radio','file','submit','button'].includes(el.type))continue;
      const r=el.getBoundingClientRect(),cs=getComputedStyle(el);if(!r.width||cs.visibility==='hidden')continue;if(parseFloat(cs.fontSize)<16)out.push((el.id||el.name)+':'+cs.fontSize)}return out""")
    ok("form fields >= 16px (no iOS/iPad zoom on focus)", not small, str(small[:6]))
    js("document.activeElement&&document.activeElement.blur()")
    stops, no_ring, body = set(), [], 0
    for _ in range(25):
        tab(); s = sig()
        if s == "body": body += 1; continue
        stops.add(s)
        r = js("const a=document.activeElement,c=getComputedStyle(a);return ((c.outlineStyle!=='none'&&parseFloat(c.outlineWidth)>0)||c.boxShadow!=='none')&&a.matches(':focus-visible')")
        if not r: no_ring.append(s)
    ok("focus ring on every Tab stop", not no_ring, f"{len(stops)} distinct stops, {body} landed on body, no ring: {no_ring[:5]}")
    # dialog
    op = d.find_elements(By.ID, "open-project-dialog"); op = op[0] if op and op[0].is_displayed() else d.find_element(By.ID, "empty-create")
    js("arguments[0].scrollIntoView({block:'center'});arguments[0].focus()", op); before = sig()
    ActionChains(d).send_keys(Keys.ENTER).perform(); wait(lambda: d.find_elements(By.CSS_SELECTOR, "#project-dialog[open]")); time.sleep(0.4)
    inside = lambda: js("return !!document.activeElement.closest('#project-dialog')")
    ok("dialog: focus starts inside", inside(), sig())
    escapes = []
    for _ in range(24):
        tab()
        if not inside(): escapes.append(sig())
    for _ in range(24):
        tab(shift=True)
        if not inside(): escapes.append(sig())
    ok("dialog: Tab stays inside (known WebKit quirk XB-4: may land on body)", not escapes, str(escapes[:5]))
    ActionChains(d).send_keys(Keys.ESCAPE).perform(); time.sleep(0.5)
    ok("dialog: Esc closes", not d.find_elements(By.CSS_SELECTOR, "#project-dialog[open]"))
    ok("dialog: focus returns to opener", sig() == before, f"before={before} after={sig()}")
    # create project
    op.click(); wait(lambda: d.find_elements(By.CSS_SELECTOR, "#project-dialog[open]"))
    name = f"Safari check {int(time.time())%100000} (synthetic)"
    d.find_element(By.ID, "project-name").send_keys(name); d.find_element(By.ID, "project-task").send_keys("Spot damaged bags (synthetic test)")
    [b for b in d.find_elements(By.CSS_SELECTOR, "#project-dialog button") if b.text.strip() == "Create project"][0].click()
    wait(lambda: not d.find_elements(By.CSS_SELECTOR, "#project-dialog[open]")); time.sleep(1.5)
    ok("create project: workspace title shows the new project", name.lower() in d.find_element(By.ID, "workspace-title").text.lower(), "address hash now " + js("return location.hash.slice(0,20)"))
    errs = js("return window.__e")
    ok("no JS errors / CSP violations after listeners attached", not errs, str(errs[:6]) + "  (WebKit prints one 'style-src-attr inline' CSP error per native <select>: XB-3, harmless)")
finally:
    print(json.dumps(R, indent=1)); d.quit()
```

## What to send back

The printed PASS/FAIL lines and the JSON. Also, by hand in the same window if you can: drag a box on a picture in the annotation dialog with a **trackpad** and on an **iPhone/iPad** (Safari → Settings → Advanced → Web Inspector is not needed; just open `http://<your-Mac-LAN-IP>:8817` after starting runserver on `0.0.0.0` **only on a trusted network and with the disposable data**), and note whether tapping a field zooms the page.

## What this still will not cover

Real iOS touch, the on-screen keyboard, Safari's address-bar resizing, VoiceOver (see the checklist in `2026-10-firefox-safari.md`), and anything on real data.
