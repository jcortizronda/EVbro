"""Web interface invariants that have broken before (they do not replace looking at it on a phone)."""
from __future__ import annotations

import re

from tests.paths import FRONTEND, SRC, frontend_css, frontend_js

WEB = FRONTEND
HTML = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = frontend_js()
CSS = frontend_css()


def visible_html() -> str:
    return re.sub(r"<!--.*?-->", "", HTML, flags=re.S)


def test_the_interface_does_not_mention_home_assistant() -> None:
    """The only place that may name Home Assistant is the card that connects to it (Ajustes → Conexiones → Home Assistant (MQTT))."""
    text = re.sub(r'<details class="[^"]*" id="mqttCard"[^>]*>.*?</details>', "", visible_html(), flags=re.S)
    assert "mqttCard" not in text
    for forbidden in ("Home Assistant", "(HA)", "Sensores HA"):
        assert forbidden not in text, forbidden


def test_the_viewport_does_not_allow_zoom() -> None:
    meta = re.search(r'<meta name="viewport" content="([^"]+)"', HTML).group(1)
    # Owner's decision (2026-10-28): the interface is «app»-like, without browser zoom.
    assert "user-scalable=no" in meta and "maximum-scale=1" in meta and "minimum-scale=1" in meta
    assert "width=device-width" in meta and "viewport-fit=cover" in meta


def test_local_scripts_exist_and_auth_loads_before_app() -> None:
    scripts = re.findall(r'<script src="(/static/[^"?]+)(?:\?v=[^"]+)?"', HTML)
    assert scripts.index("/static/js/core/auth.js") < scripts.index("/static/js/core/state.js")
    for path in scripts:
        assert (WEB / path.removeprefix("/")).is_file(), path
    for sheet in re.findall(r'href="(/static/[^"?]+)', HTML):
        assert (WEB / sheet.removeprefix("/")).is_file(), sheet


def test_figures_with_a_unit_do_not_repeat_it() -> None:
    """The HTML already puts `km` next to the big number: the JS must write only the number (it showed «60 km km»)."""
    for ident in ("electricRange", "fuelRange"):
        assert f'id="{ident}">--</span>' in HTML
    assert "getElementById('electricRange');\n    if (el) el.innerText = `${Math.round(data.electric_range_km)}`;" in APP_JS
    assert "getElementById('fuelRange');\n    if (el) el.innerText = `${Math.round(data.fuel_range_km)}`;" in APP_JS


def test_the_pin_is_not_stored_in_the_browser() -> None:
    assert "setItem('ebro_user_pin'" not in APP_JS


def test_there_is_no_inline_code_in_the_interface() -> None:
    """The CSP does not allow `onclick="…"` or inline `<script>`: handlers are declared with `data-on-*` (core/actions.js).
    A new inline handler would not run in the browser; this test says so first."""
    for name, text in (("index.html", visible_html()), ("JS", APP_JS)):
        inline = re.findall(r'\son(?:click|change|input|submit|keydown|keyup|load|error|focus|blur)="[^"]*"', text)
        assert not inline, f"Inline handlers in {name}: {inline[:5]}"
    # The only script without `src` is the JSON data block of the dictionary (`type="application/json"`): the browser never runs it.
    assert not re.search(r'<script(?![^>]*\bsrc=)(?![^>]*type="application/json")[^>]*>', visible_html()), "inline <script> in index.html"
    assert 'href="javascript:' not in HTML
    scripts = re.findall(r'<script src="(/static/[^"?]+)', HTML)
    assert scripts.index("/static/js/core/actions.js") < scripts.index("/static/js/features/history-list.js")


def test_declarative_handlers_are_simple_calls() -> None:
    """`data-on-*` only accepts `function(literals | this | this.value | this.checked | event)` separated by «;»."""
    call = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\((?:[^()\"']|'[^']*'|\"[^\"]*\")*\)"
    shape = re.compile(rf"^\s*{call}(?:\s*;\s*{call})*\s*;?\s*$")
    bad = [v for v in re.findall(r'data-on-(?:click|change|input|enter)="([^"]*)"', visible_html()) if not shape.match(v)]
    assert not bad, bad


def test_user_text_does_not_enter_the_html_unescaped() -> None:
    """Audit 2026-10: maintenance notes, geofence names and alert messages were interpolated as-is into `innerHTML`
    (a linked account with edit permission could slip a script into the manager's session). Every text from a user or an external
    service goes through `escapeHtml()`; this list holds the fields that were already escaped once."""
    pattern = re.compile(r"\$\{(?:l|gf|it|s|c|d|m|x|e|r|doc|log|row)\.(?:notes|name|title|message|workshop|item_name|address|username|station|error)\}")
    # `showToast`, `confirm`, `prompt` and errors do not build HTML (the toast escapes what it receives itself)
    safe = ("showToast(", "confirm(", "prompt(", "alert(", "console.", "new Error(", "throw ", "textContent", "innerText", ".value =")
    raw = [ln.strip() for ln in APP_JS.splitlines() if pattern.search(ln) and not any(s in ln for s in safe)]
    assert not raw, f"Text interpolated into HTML without escapeHtml(): {raw}"


def test_the_service_worker_only_precaches_files_that_exist() -> None:
    sw = (WEB / "static" / "sw.js").read_text(encoding="utf-8")
    local = re.findall(r"'(/static/[^']+)'", sw)
    assert "/static/js/core/auth.js" in local
    for path in local:
        assert (WEB / path.removeprefix("/")).is_file(), path


def test_the_mobile_layer_covers_what_used_to_overflow() -> None:
    """Header, tables as cards and 44 px touch buttons (see frontend/static/css/layout/mobile-layer.css)."""
    assert "MOBILE LAYER" in CSS
    for rule in (".brand-copy { display: none; }", "table.data-table td::before", "--tap: 44px"):
        assert rule in CSS, rule


def test_the_tab_is_called_confort() -> None:
    """Climate, seats, steering wheel and windows: «Clima» fell short. The internal ids are still `climate`."""
    text = visible_html()
    assert text.count("<span>Confort</span>") == 2 and "<span>Clima</span>" not in text    # desktop sidebar + touch dock
    for ident in ("comfortDefrost", "comfortSeats", "btnClimatePowerToggle"):
        assert f'id="{ident}"' in text, ident
    assert "window._driverSeatHeaterOn" not in APP_JS, "the button state comes from the car, not from local flags"


def test_the_headers_carry_a_small_image() -> None:
    """Panoramic ~100 px strip with a light image (the original photos weighed ~800 KB and pushed the content down)."""
    text = visible_html()
    assert "native-hero-banner" not in text and "native-hero-banner" not in CSS
    # The banners used to carry `style="--banner: url(...)"`; now it is a generated utility class (tools/inline_styles.py)
    images = re.findall(r"--banner: url\('(/static/images/[^']+)'\)", CSS)
    assert len(images) == 3 and text.count("page-banner u---banner-url-") == 3
    for path in images:
        file = WEB / path.removeprefix("/")
        assert file.is_file() and file.stat().st_size < 100_000, f"{path} is too heavy for a strip"
    assert re.search(r"\.page-banner \{[^}]*min-height: 9\d?px", CSS)
    for ident in ("vehicleName", "vehicleStatusBadge", "vehicleVin", "heroPlateBadge", "energyHeroTitle",
                  "chargeHeroStateBadge", "energyHeroPlateBadge", "climateHeroTitle", "climateStatusBadge"):
        assert f'id="{ident}"' in text, ident


def test_vent_is_no_longer_on_the_home_page_because_it_is_in_windows() -> None:
    assert "toggleVentWindows" not in visible_html() and "toggleVentWindows" not in APP_JS
    assert "ventilar_ventanillas" in APP_JS            # still available in the Windows sheet


def test_home_page_tiles_change_icon_and_colour() -> None:
    home = (WEB / "static" / "js" / "features" / "home.js").read_text(encoding="utf-8")
    for state in ("closed", "open", "unknown"):
        assert f'data-state="{state}"]' in CSS or f'[data-state="{state}"] .card-icon-wrap' in CSS
    for tile in ("lock", "trunk", "windows", "sunroof"):
        assert re.search(rf"{tile}: \{{[^}}]*closed:[^}}]*open:", home, flags=re.S), tile
    assert ".card-icon-wrap" in CSS and "background" not in re.search(
        r'\.native-card-btn\[data-state="open"\] \.card-icon-wrap \{[^}]*\}', CSS).group(0), "the colour goes on the icon, not on the button"


def test_temperature_presets_are_the_climate_control_limits() -> None:
    text = visible_html()
    assert "comfortPreset(16)" in text and "comfortPreset(30)" in text and "comfortPreset(22)" in text
    assert 'id="climateSlider" min="16" max="30"' in text        # 16 and 30 are the slider limits


def test_home_and_energy_switches_use_the_real_state() -> None:
    text = visible_html()
    assert 'data-comfort-id="alarm"' in text
    assert 'data-comfort-id="charge"' not in text and 'data-comfort-id="schedule"' not in text
    for flag in ("window._alarmOn", "window._chargeScheduleOn", "toggleAlarmState", "toggleChargeScheduleAction"):
        assert flag not in APP_JS and flag not in text, flag


def test_unknown_state_is_not_shown_as_closed() -> None:
    assert "Sin datos" in APP_JS and "openClosedText" in APP_JS
    assert APP_JS.count("function toggleLockState") == 1, "toggleLockState was defined twice (the last one won)"


def test_app_js_does_not_lose_key_functions() -> None:
    """A botched trim once deleted showToast and the action sheets: each function in the list must exist exactly once."""
    for name in ("showToast", "openActionSheet", "closeActionSheet", "openTrunkActionSheet", "openWindowsActionSheet",
                 "openSunroofActionSheet", "executeCommandDirect", "executePendingCommand", "applyTelemetry"):
        assert len(re.findall(rf"^(?:async )?function {name}\b", APP_JS, flags=re.M)) == 1, name


def test_charge_control_uses_the_server_and_there_is_no_toy_limiter() -> None:
    """Stop/start/schedule/target go to the server's charge manager; the old «Limitar carga» only showed a notice."""
    text = visible_html()
    for ident in ("energyTargetSlider", "btnChargeStart", "btnChargeStop", "scheduleForm", "schedStart", "schedEnd",
                  "schedHours", "chargeStatusLine", "scheduleStatus", "scheduleBadge"):
        assert f'id="{ident}"' in text, ident
    assert "chargeLimitSlider" not in text and "setChargeLimit" not in APP_JS and "accChargeLimit" not in text
    charge_js = (WEB / "static" / "js" / "features" / "charge-control.js").read_text(encoding="utf-8")
    for route in ("'target'", "'start'", "'stop'", "'schedule'"):
        assert route in charge_js, route
    assert "carga_iniciar" not in charge_js, "Ebro does not accept starting/stopping the charge (A00084)"


def test_every_script_on_disk_is_loaded_and_every_loaded_script_exists() -> None:
    """A new file in js/ that is forgotten in index.html would not run (silently): both directions are checked."""
    loaded = {r for r in re.findall(r'<script src="(/static/js/[^"?]+)', HTML)}
    on_disk = {"/" + f.relative_to(WEB).as_posix() for f in (WEB / "static" / "js").rglob("*.js")}
    assert loaded == on_disk, (on_disk - loaded, loaded - on_disk)
    sw = (WEB / "static" / "sw.js").read_text(encoding="utf-8")
    assert {r for r in re.findall(r"'(/static/js/[^']+)'", sw)} == on_disk, "the SW precache does not match js/"


def test_the_asset_version_is_computed_by_the_server() -> None:
    """`?v=` comes from the content hash (jinja `asset_version`), not from hand-written numbers."""
    assert "{{ asset_version }}" in HTML
    assert not re.findall(r'\?v=\d+\.\d+', HTML), "hand-written versions remain in the HTML"


def test_every_stylesheet_on_disk_is_linked_and_precached() -> None:
    linked = set(re.findall(r'href="(/static/css/[^"?]+)', HTML))
    on_disk = {"/" + f.relative_to(WEB).as_posix() for f in (WEB / "static" / "css").rglob("*.css")}
    assert linked == on_disk, (on_disk - linked, linked - on_disk)
    sw = (WEB / "static" / "sw.js").read_text(encoding="utf-8")
    assert set(re.findall(r"'(/static/css/[^']+)'", sw)) == on_disk


def test_the_interface_uses_no_emojis() -> None:
    """Owner's decision (2026-10-28): no emojis in the texts; where an icon helps, SVG (`core/icons.js`).
    `core/icons.js` names them with escape codes (to strip them from the server's messages), not literals."""
    import re
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-✒✔✖✘-➿⭐⬆⬇⌀-⏿]")
    files = [WEB / "templates" / "index.html", *(WEB / "static").rglob("*.js"), *(WEB / "static").rglob("*.css")]
    files.append(SRC / "services" / "notification_service.py")
    dirty = {f.name: sorted(set(emoji.findall(f.read_text(encoding="utf-8")))) for f in files}
    dirty = {k: v for k, v in dirty.items() if v}
    assert not dirty, dirty


def test_there_are_no_inline_styles_in_the_interface() -> None:
    """The page's CSP does not accept `style="…"` (neither static nor in JS templates): tools/inline_styles.py turns them into classes
    and the dynamic ones go in `data-css` + applyDataStyles() (CSSOM). The tool's `--check` must stay green."""
    static_style = re.compile(r'\sstyle="([^"${}]*)"')
    offenders = [str(p.relative_to(FRONTEND)) for p in [FRONTEND / "templates" / "index.html", *(FRONTEND / "static" / "js").rglob("*.js")]
                 if static_style.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"inline style=\"…\" in {offenders}: run python tools/inline_styles.py --apply"
    assert "style=\"" not in visible_html() and "setAttribute('style'" not in APP_JS
    assert "utilities.css" in HTML and "applyDataStyles" in APP_JS


def test_the_app_never_uses_the_browser_dialogs() -> None:
    """alert/confirm/prompt look foreign on the phone and block the page: the app uses its own dialog (core/feedback.js) or a toast."""
    import re

    files = list((WEB / "static" / "js").rglob("*.js")) + list((WEB / "admin" / "assets").glob("*.js"))
    offenders = [f.name for f in sorted(files) if re.search(r"(?<![.\w])(alert|confirm|prompt)\(", f.read_text(encoding="utf-8"))]
    assert not offenders, f"browser dialogs in {offenders}: use confirmDialog/promptDialog/alertDialog or showToast (ask() in the console)"
    assert 'id="appDialog"' in HTML and "appDialogAnswer" in HTML
    assert 'id="dlgAsk"' in (WEB / "admin" / "index.html").read_text(encoding="utf-8")


def test_every_declarative_handler_names_a_function_that_exists() -> None:
    """`data-on-click="closeModal('journeyModal')"` shipped once with no `closeModal` anywhere: the Cancel button of the journey
    form did nothing (2026-10-31). Every `data-on-*` call in the HTML, and every one the JS renders into templates, must resolve to
    a global function or to a method of a global object."""
    calls = set(re.findall(r'data-on-[a-z]+="([^"]*)"', visible_html()))
    calls |= set(re.findall(r'data-on-[a-z]+=\\?"([^"\\$]*)', APP_JS))
    names = {m for c in calls for m in re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", c)}
    missing = []
    for name in sorted(names):
        if name.startswith(("document.", "window.", "event.", "this.", "location.", "history.", "navigator.")):
            continue
        if "." in name:
            obj, method = name.split(".", 1)
            defined = re.search(rf"(?:window\.|\b(?:const|let|var)\s+){re.escape(obj)}\b", APP_JS) and re.search(rf"\b{re.escape(method)}\b", APP_JS)
        else:
            defined = re.search(rf"(?:\bfunction\s+{re.escape(name)}\b|window\.{re.escape(name)}\s*=|\b(?:const|let|var)\s+{re.escape(name)}\s*=)", APP_JS)
        if not defined:
            missing.append(name)
    assert not missing, f"Declarative handlers with no function in the JavaScript: {missing}"
