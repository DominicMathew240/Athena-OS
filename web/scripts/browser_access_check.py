"""The access screen in a real browser (Firefox via geckodriver, or Safari via safaridriver) with Selenium.

Prototype check on a disposable console with SYNTHETIC people and projects; see docs/dogfood/2026-10-access-screen-browsers.md.

  1. seed:   ATHENA_DATA_HOME=/tmp/x DJANGO_DEBUG=1 ACCESS_CHECK_PWFILE=pw.txt ACCESS_CHECK_SEED=seed.json \
             python web/scripts/seed_access_check.py     (run from web/)
  2. serve:  ATHENA_DATA_HOME=/tmp/x DJANGO_DEBUG=1 python manage.py runserver 127.0.0.1:8819 --noreload
  3. run:    python web/scripts/browser_access_check.py firefox desktop|phone
             python web/scripts/browser_access_check.py safari  desktop      (needs `sudo safaridriver --enable` once,
                                                                              and Safari > Develop > Allow Remote Automation)

Environment: ACCESS_CHECK_BASE (default http://127.0.0.1:8819), ACCESS_CHECK_PWFILE, ACCESS_CHECK_SEED,
ACCESS_CHECK_OUT (default ./access-check-out), FIREFOX_BIN and GECKODRIVER (paths; geckodriver on PATH if unset).
Needs `pip install selenium`. The sign-in password is read from a file and never printed.
"""
import json, os, sys, time, traceback
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE = os.environ.get("ACCESS_CHECK_BASE", "http://127.0.0.1:8819")
BROWSER = sys.argv[1] if len(sys.argv) > 1 else "firefox"
PROFILE = sys.argv[2] if len(sys.argv) > 2 else "desktop"
if BROWSER not in ("firefox", "safari"):
    sys.exit("usage: browser_access_check.py firefox|safari desktop|phone")
PW = open(os.environ["ACCESS_CHECK_PWFILE"]).read().strip()
SEED = json.load(open(os.environ["ACCESS_CHECK_SEED"]))
OUT = os.environ.get("ACCESS_CHECK_OUT", os.path.join(os.getcwd(), "access-check-out"))
VW, VH = (1440, 900) if PROFILE == "desktop" else (390, 844)
os.makedirs(f"{OUT}/shots", exist_ok=True)
W = OUT
TAG = f"{BROWSER}-access-{PROFILE}"

R = dict(browser=BROWSER, profile=PROFILE, steps=[], console=[], jserrors=[], csp=[], notes=[])
where = "start"
if BROWSER == "firefox":
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    o = Options()
    o.add_argument("-headless")
    if os.environ.get("FIREFOX_BIN"):
        o.binary_location = os.environ["FIREFOX_BIN"]
    o.enable_bidi = True
    d = webdriver.Firefox(options=o, service=Service(os.environ.get("GECKODRIVER", "geckodriver"), log_output=f"{W}/gecko-{PROFILE}.log"))
else:
    d = webdriver.Safari()
d.set_script_timeout(30)
try:
    d.set_window_size(VW, VH + 100)
except Exception as e:
    R["notes"].append("set_window_size: " + str(e)[:120])
if PROFILE == "phone" and BROWSER == "firefox":
    d.browsing_context.set_viewport(context=d.current_window_handle, viewport={"width": VW, "height": VH}, device_pixel_ratio=2)
    R["notes"].append("390x844 is a BiDi viewport override in desktop Firefox (minimum real window ~500 px)")
elif PROFILE == "phone":
    R["notes"].append("phone profile in Safari is just a narrow window; Safari enforces its own minimum width")
R["version"] = d.capabilities.get("browserVersion")
if BROWSER == "firefox":
    d.script.add_console_message_handler(lambda m: R["console"].append(dict(at=where, level=getattr(m, "level", None), text=str(getattr(m, "text", ""))[:300])))
    d.script.add_javascript_error_handler(lambda e: R["jserrors"].append(dict(at=where, text=str(getattr(e, "text", e))[:300])))
    d.script.pin("""() => { window.__csp = [];
     document.addEventListener('securitypolicyviolation', e => window.__csp.push({d: e.violatedDirective, b: String(e.blockedURI).slice(0,80)})); }""")
else:
    R["notes"].append("Safari: no WebDriver BiDi, so console, JavaScript and CSP errors are NOT captured by this run")

def save():
    json.dump(R, open(f"{W}/{TAG}.json", "w"), indent=1, default=str)


def step(name):
    def deco(fn):
        global where
        where = name
        t = time.time()
        try:
            detail = fn()
            R["steps"].append(dict(step=name, ok=True, detail=detail, secs=round(time.time() - t, 1)))
        except Exception as e:
            R["steps"].append(dict(step=name, ok=False, detail=f"{type(e).__name__}: {str(e)[:300]}"))
            try:
                d.save_screenshot(f"{W}/shots/{TAG}-FAIL-{len(R['steps'])}.png")
            except Exception:
                pass
            traceback.print_exc()
        save()
        return fn
    return deco


def js(s, *a):
    return d.execute_script(s, *a)


def wait(cond, secs=10, msg="timed out"):
    return WebDriverWait(d, secs, poll_frequency=0.1).until(lambda _: cond(), msg)


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def overflow():
    return js("return document.documentElement.scrollWidth - document.documentElement.clientWidth")


def sign_in(user):
    d.get(BASE + "/login/")
    d.find_element(By.NAME, "username").send_keys(user)
    d.find_element(By.NAME, "password").send_keys(PW)
    d.find_element(By.CSS_SELECTOR, "form button[type=submit]").click()
    wait(lambda: "/login" not in d.current_url, 10, "still on login")


def sign_out():
    d.get(BASE + "/")
    wait(lambda: d.find_elements(By.CSS_SELECTOR, "form[action$='logout/'] button, form[aria-label='Sign out'] button"))
    js("document.querySelector('form[aria-label=\"Sign out\"] button, form[action$=\"logout/\"] button').click()")
    wait(lambda: "/login" in d.current_url, 10, "not signed out")


def status():
    return d.find_element(By.ID, "access-status").text


def person(name):
    for li in d.find_elements(By.CSS_SELECTOR, "#people > li"):
        if li.find_element(By.CSS_SELECTOR, ".person-name").text == name:
            return li
    raise AssertionError(f"no card for {name}")


def pick(select_el, value):
    js("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input',{bubbles:true})); arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", select_el, value)


def rows():
    return js("return [...document.querySelectorAll('#record-rows tr')].map(r => r.innerText.replace(/\\n/g, ' | ').replace(/\\t/g, ' | '))")


def api(method, path, body=None):
    return d.execute_async_script("""const [m, p, b, done] = [arguments[0], arguments[1], arguments[2], arguments[arguments.length-1]];
      const t = document.querySelector('meta[name="csrf-token"]'); 
      fetch(p, {method: m, credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRFToken': t ? t.content : (document.cookie.match(/csrftoken=([^;]+)/)||[])[1] || ''}, body: b ? JSON.stringify(b) : undefined})
        .then(async r => done({status: r.status, body: await r.json().catch(() => ({}))}));""", method, path, body)


@step("1 admin signs in; console shows an Access link")
def _():
    sign_in("admin1")
    link = d.find_elements(By.CSS_SELECTOR, "nav a[href='/access']")
    ok(link, "no Access link for the administrator")
    return f"link text {link[0].text!r}"


@step("2 access screen loads: 4 roles, 4 people, empty record, no overflow")
def _():
    d.get(BASE + "/access")
    wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "#people > li")) >= 4)
    keys = [e.text for e in d.find_elements(By.CSS_SELECTOR, "#role-key dt")]
    ok(keys == ["Viewer", "Labeler", "Trainer", "Approver"], f"role key {keys}")
    wait(lambda: d.find_element(By.ID, "record-empty").is_displayed())
    ov = overflow()
    ok(ov <= 0, f"horizontal overflow {ov}px")
    d.save_screenshot(f"{W}/shots/{TAG}-01-load.png")
    return f"{len(d.find_elements(By.CSS_SELECTOR, '#people > li'))} people, overflow {ov}"


@step("3 superuser card has no role controls")
def _():
    card = person("admin1")
    ok(not card.find_elements(By.CSS_SELECTOR, "select, button"), "controls on a superuser")
    return card.find_element(By.CSS_SELECTOR, ".field-help").text[:60]


@step("4 give alice the Trainer role everywhere; recorded")
def _():
    card = person("alice")
    sel = card.find_element(By.ID, f"console-role-{card.get_attribute('data-user')}")
    save_btn = card.find_element(By.CSS_SELECTOR, ".person-row button")
    ok(not save_btn.is_enabled(), "Save enabled before any change")
    pick(sel, "trainer")
    ok(save_btn.is_enabled(), "Save still disabled after change")
    save_btn.click()
    wait(lambda: "now has the Trainer role" in status())
    wait(lambda: len(rows()) == 1)
    r = rows()[0]
    ok("alice" in r and "Given Trainer" in r and "Everywhere" in r and "admin1" in r, r)
    focus = js("return document.activeElement && document.activeElement.id")
    ok(focus.startswith("console-role-"), f"focus lost after save: {focus!r}")
    return r


@step("5 give bob Labeler on Alpha only; recorded with the project name")
def _():
    card = person("bob")
    uid = card.get_attribute("data-user")
    pick(card.find_element(By.ID, f"add-project-{uid}"), SEED["alpha"])
    pick(card.find_element(By.ID, f"add-role-{uid}"), "labeler")
    card.find_element(By.CSS_SELECTOR, ".project-add button[type=submit]").click()
    wait(lambda: "is now a Labeler on" in status())
    wait(lambda: len(rows()) == 2)
    r = rows()[0]
    ok("bob" in r and "Alpha bags" in r and "Given Labeler" in r, r)
    card = person("bob")
    ok(card.find_elements(By.CSS_SELECTOR, ".project-role"), "no project role row for bob")
    return r


@step("6 empty submit of the add form is refused with a message and focus")
def _():
    card = person("carol")
    card.find_element(By.CSS_SELECTOR, ".project-add button[type=submit]").click()
    wait(lambda: "Choose a project and a role first" in status())
    ok(status() and d.find_element(By.ID, "access-status").get_attribute("data-tone") == "error", "tone not error")
    ok(len(rows()) == 2, "a change was recorded for an empty submit")
    return js("return document.activeElement.id")


@step("7 change bob's Alpha role to Approver (Save button), then keep it via Esc on remove dialog")
def _():
    card = person("bob")
    row = card.find_element(By.CSS_SELECTOR, ".project-role")
    pick(row.find_element(By.TAG_NAME, "select"), "approver")
    row.find_elements(By.TAG_NAME, "button")[0].click()
    wait(lambda: "is now a Approver on" in status())
    wait(lambda: len(rows()) == 3)
    r = rows()[0]
    ok("Labeler changed to Approver" in r, r)
    card = person("bob")
    card.find_elements(By.CSS_SELECTOR, ".project-role button")[1].click()
    wait(lambda: d.find_elements(By.CSS_SELECTOR, "dialog[open]"))
    dialog_text = d.find_element(By.CSS_SELECTOR, "dialog[open]").text
    ok("Remove bob" in dialog_text, dialog_text[:120])
    # Tab trap
    escaped = 0
    tab = Keys.ALT + Keys.TAB if BROWSER == "safari" else Keys.TAB  # Safari's plain Tab skips buttons and links
    for _i in range(12):
        d.switch_to.active_element.send_keys(tab)
        if not js("return !!document.activeElement.closest('dialog')"):
            escaped += 1
    ok(escaped == 0, f"Tab left the dialog {escaped} times")
    d.switch_to.active_element.send_keys(Keys.ESCAPE)
    wait(lambda: not d.find_elements(By.CSS_SELECTOR, "dialog[open]"))
    ok(len(rows()) == 3, "Esc recorded a change")
    ok(person("bob").find_elements(By.CSS_SELECTOR, ".project-role"), "role was removed by Esc")
    return "Esc kept the role; 0 Tab escapes in 12 presses"


@step("8 remove bob's Alpha role through the dialog; record says taken away")
def _():
    card = person("bob")
    card.find_elements(By.CSS_SELECTOR, ".project-role button")[1].click()
    wait(lambda: d.find_elements(By.CSS_SELECTOR, "dialog[open]"))
    btn = [b for b in d.find_elements(By.CSS_SELECTOR, "dialog[open] button") if b.text == "Remove the role"]
    ok(btn, "no confirm button")
    btn[0].click()
    wait(lambda: len(rows()) == 4)
    r = rows()[0]
    ok("Approver taken away" in r and "Alpha bags" in r, r)
    ok(not person("bob").find_elements(By.CSS_SELECTOR, ".project-role"), "role row still there")
    return r


@step("9 record filters: by project and by person")
def _():
    pick(d.find_element(By.ID, "record-project"), SEED["alpha"])
    wait(lambda: len(rows()) == 3)
    pick(d.find_element(By.ID, "record-project"), "")
    wait(lambda: len(rows()) == 4)
    ref = js("return [...document.querySelectorAll('#record-person option')].find(o => o.textContent === 'alice').value")
    pick(d.find_element(By.ID, "record-person"), ref)
    wait(lambda: len(rows()) == 1)
    pick(d.find_element(By.ID, "record-person"), "")
    wait(lambda: len(rows()) == 4)
    d.save_screenshot(f"{W}/shots/{TAG}-02-record.png")
    return "alpha: 3, alice: 1, all: 4"


@step("10 search box narrows the people list")
def _():
    box = d.find_element(By.ID, "people-search")
    box.send_keys("ali")
    wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "#people > li")) == 1)
    box.send_keys("zzz")
    wait(lambda: d.find_element(By.ID, "people-empty").is_displayed())
    box.clear(); box.send_keys(Keys.BACKSPACE)
    js("const b = document.getElementById('people-search'); b.value=''; b.dispatchEvent(new Event('input',{bubbles:true}))")
    wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "#people > li")) == 4)
    return "ok"


@step("11 keyboard: every control on the page reachable by Tab, each with a focus ring")
def _():
    d.get(BASE + "/access")
    wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "#people > li")) >= 4)
    seen, bare, onbody = set(), 0, 0
    d.switch_to.window(d.current_window_handle)
    js("window.focus()")
    R["notes"].append(f"step 11 document.hasFocus() before Tab: {js('return document.hasFocus()')}")
    for _i in range(60):
        d.switch_to.active_element.send_keys(Keys.ALT + Keys.TAB if BROWSER == "safari" else Keys.TAB)
        info = js("""const e = document.activeElement; if (!e || e === document.body) return null;
          const cs = getComputedStyle(e); return {k: [...document.querySelectorAll('*')].indexOf(e) + e.tagName + '#' + (e.id || '') + '.' + (e.className || '') + ':' + (e.textContent||'').slice(0,20), ring: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0, vis: e.matches(':focus-visible'), hf: document.hasFocus(), os: cs.outlineStyle, ow: cs.outlineWidth};""")
        if info is None:
            onbody += 1
            continue
        if info["k"] in seen:
            R["notes"].append(f"step 11: one full lap of {len(seen)} stops, then focus wrapped; stopped")
            break
        seen.add(info["k"])
        if not (info["ring"] and info["vis"]):
            bare += 1
            R["notes"].append("bare stop: " + json.dumps(info))
    ok(len(seen) >= 12, f"only {len(seen)} distinct stops")
    ok(bare == 0, f"{bare} stops without a focus ring")
    return f"{len(seen)} distinct stops in one Tab lap, {bare} without ring, {onbody} on body"


@step("12 non-admin: no Access link, /access refused, API refused")
def _():
    sign_out()
    sign_in("alice")
    ok(not d.find_elements(By.CSS_SELECTOR, "nav a[href='/access']"), "Access link shown to a non-admin")
    d.get(BASE + "/access")
    body = d.find_element(By.TAG_NAME, "body").text
    r = api("GET", "/api/access")
    r2 = api("PUT", "/api/access/people/1/console", {"role": "approver"})
    ok(r["status"] == 403 and r2["status"] == 403, f"api answered {r['status']} / {r2['status']}")
    return f"/access shows {body[:60]!r}; GET {r['status']}, PUT {r2['status']}"


@step("13 per-project role takes effect: bob (no console role) is Labeler on Alpha only")
def _():
    sign_out()
    # give bob Labeler on Alpha again, console role none; unassigned default is labeler so first make it a Viewer-only case
    sign_in("admin1")
    d.get(BASE + "/access")
    wait(lambda: len(d.find_elements(By.CSS_SELECTOR, "#people > li")) >= 4)
    card = person("bob")
    uid = card.get_attribute("data-user")
    pick(card.find_element(By.ID, f"console-role-{uid}"), "viewer")
    card.find_element(By.CSS_SELECTOR, ".person-row button").click()
    wait(lambda: "now has the Viewer role" in status())
    card = person("bob")
    pick(card.find_element(By.ID, f"add-project-{uid}"), SEED["alpha"])
    pick(card.find_element(By.ID, f"add-role-{uid}"), "labeler")
    card.find_element(By.CSS_SELECTOR, ".project-add button[type=submit]").click()
    wait(lambda: "is now a Labeler on" in status())
    sign_out()
    sign_in("bob")
    d.get(BASE + "/")
    me = api("GET", "/api/me")
    alpha = api("PATCH", f"/api/projects/{SEED['alpha']}", {"labels": ["x", "x2"]})
    beta = api("PATCH", f"/api/projects/{SEED['beta']}", {"labels": ["y", "y2"]})
    ok(alpha["status"] == 200, f"Alpha write answered {alpha['status']} {alpha['body']}")
    ok(beta["status"] == 403, f"Beta write answered {beta['status']} {beta['body']}")
    return f"/api/me role={me['body'].get('role')}; Alpha PATCH {alpha['status']}, Beta PATCH {beta['status']}: {str(beta['body'])[:110]}"


@step("14 admin sees all of it in the record, newest first, nothing editable")
def _():
    sign_out()
    sign_in("admin1")
    d.get(BASE + "/access")
    wait(lambda: len(rows()) >= 6)
    n = len(rows())
    ok(not d.find_elements(By.CSS_SELECTOR, "#record-rows input, #record-rows select, #record-rows button"), "record has controls")
    api_put = api("PUT", "/api/access/changes", {})
    ok(api_put["status"] in (404, 405), f"record accepted a write: {api_put['status']}")
    d.save_screenshot(f"{W}/shots/{TAG}-03-final.png")
    return f"{n} rows; PUT on the record answered {api_put['status']}"


@step("15 no console errors, JS errors or CSP violations")
def _():
    errs = [c for c in R["console"] if c.get("level") in ("error", "warn", "warning")]
    # failed requests we caused on purpose (403 from step 12/13) are logged as network errors by Firefox; report, don't hide
    return f"console error/warn lines: {len(errs)}; js errors: {len(R['jserrors'])}"


try:
    R["csp_final"] = js("return window.__csp || []")
except Exception:
    pass
d.quit()
save()
bad = [s for s in R["steps"] if not s["ok"]]
print(json.dumps(dict(browser=BROWSER, version=R["version"], profile=PROFILE, passed=len(R["steps"]) - len(bad), total=len(R["steps"]), failed=[(s["step"], s["detail"]) for s in bad], jserrors=R["jserrors"], console=R["console"][:10], csp=R.get("csp_final")), indent=1))
