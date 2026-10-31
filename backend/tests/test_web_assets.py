"""Invariantes de la interfaz web que han fallado antes (no sustituyen a mirarla en un móvil)."""
from __future__ import annotations

import re

from tests.paths import FRONTEND, SRC, frontend_css, frontend_js

WEB = FRONTEND
HTML = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = frontend_js()
CSS = frontend_css()


def visible_html() -> str:
    return re.sub(r"<!--.*?-->", "", HTML, flags=re.S)


def test_la_interfaz_no_menciona_home_assistant() -> None:
    """The only place that may name Home Assistant is the card that connects to it (Ajustes → Cuenta → Home Assistant (MQTT))."""
    texto = re.sub(r'<div class="glass details-card needs-root u-margin-bottom-1rem" id="mqttCard">.*?</div>\s*</div>\s*</div>', "", visible_html(), flags=re.S)
    assert "mqttCard" not in texto
    for prohibido in ("Home Assistant", "(HA)", "Sensores HA"):
        assert prohibido not in texto, prohibido


def test_el_viewport_no_permite_zoom() -> None:
    meta = re.search(r'<meta name="viewport" content="([^"]+)"', HTML).group(1)
    # Decisión del dueño (2026-10-28): la interfaz es de tipo «app», sin zoom del navegador.
    assert "user-scalable=no" in meta and "maximum-scale=1" in meta and "minimum-scale=1" in meta
    assert "width=device-width" in meta and "viewport-fit=cover" in meta


def test_los_scripts_locales_existen_y_auth_va_antes_que_app() -> None:
    scripts = re.findall(r'<script src="(/static/[^"?]+)(?:\?v=[^"]+)?"', HTML)
    assert scripts.index("/static/js/core/auth.js") < scripts.index("/static/js/core/state.js")
    for ruta in scripts:
        assert (WEB / ruta.removeprefix("/")).is_file(), ruta
    for hoja in re.findall(r'href="(/static/[^"?]+)', HTML):
        assert (WEB / hoja.removeprefix("/")).is_file(), hoja


def test_las_cifras_con_unidad_no_la_repiten() -> None:
    """El HTML ya pone `km` junto al número grande: el JS solo debe escribir el número (salía «60 km km»)."""
    for ident in ("electricRange", "fuelRange"):
        assert f'id="{ident}">--</span>' in HTML
    assert "getElementById('electricRange');\n    if (el) el.innerText = `${Math.round(data.electric_range_km)}`;" in APP_JS
    assert "getElementById('fuelRange');\n    if (el) el.innerText = `${Math.round(data.fuel_range_km)}`;" in APP_JS


def test_el_pin_no_se_guarda_en_el_navegador() -> None:
    assert "setItem('ebro_user_pin'" not in APP_JS


def test_no_hay_codigo_en_linea_en_la_interfaz() -> None:
    """La CSP no permite `onclick="…"` ni `<script>` en línea: los manejadores se declaran con `data-on-*` (core/actions.js).
    Un manejador en línea nuevo no se ejecutaría en el navegador; este test lo dice antes."""
    for nombre, texto in (("index.html", visible_html()), ("JS", APP_JS)):
        en_linea = re.findall(r'\son(?:click|change|input|submit|keydown|keyup|load|error|focus|blur)="[^"]*"', texto)
        assert not en_linea, f"Manejadores en línea en {nombre}: {en_linea[:5]}"
    # The only script without `src` is the JSON data block of the dictionary (`type="application/json"`): the browser never runs it.
    assert not re.search(r'<script(?![^>]*\bsrc=)(?![^>]*type="application/json")[^>]*>', visible_html()), "<script> en línea en index.html"
    assert 'href="javascript:' not in HTML
    scripts = re.findall(r'<script src="(/static/[^"?]+)', HTML)
    assert scripts.index("/static/js/core/actions.js") < scripts.index("/static/js/features/history-list.js")


def test_los_manejadores_declarativos_son_llamadas_simples() -> None:
    """`data-on-*` solo admite `funcion(literales | this | this.value | this.checked | event)` separadas por «;»."""
    call = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\((?:[^()\"']|'[^']*'|\"[^\"]*\")*\)"
    forma = re.compile(rf"^\s*{call}(?:\s*;\s*{call})*\s*;?\s*$")
    malos = [v for v in re.findall(r'data-on-(?:click|change|input|enter)="([^"]*)"', visible_html()) if not forma.match(v)]
    assert not malos, malos


def test_el_texto_del_usuario_no_entra_en_el_html_sin_escapar() -> None:
    """Auditoría 2026-10: notas de mantenimiento, nombres de geocerca y mensajes de avisos se interpolaban tal cual en `innerHTML`
    (una cuenta asociada con permiso de editar podía colar un script en la sesión del gestor). Todo texto de usuario o de un servicio
    externo pasa por `escapeHtml()`; esta lista son los campos que ya se escaparon una vez."""
    patron = re.compile(r"\$\{(?:l|gf|it|s|c|d|m|x|e|r|doc|log|row)\.(?:notes|name|title|message|workshop|item_name|address|username|station|error)\}")
    # `showToast`, `confirm`, `prompt` y los errores no construyen HTML (el toast escapa él mismo lo que recibe)
    seguras = ("showToast(", "confirm(", "prompt(", "alert(", "console.", "new Error(", "throw ", "textContent", "innerText", ".value =")
    raw = [ln.strip() for ln in APP_JS.splitlines() if patron.search(ln) and not any(s in ln for s in seguras)]
    assert not raw, f"Texto interpolado en HTML sin escapeHtml(): {raw}"


def test_el_service_worker_solo_precachea_ficheros_que_existen() -> None:
    sw = (WEB / "static" / "sw.js").read_text(encoding="utf-8")
    locales = re.findall(r"'(/static/[^']+)'", sw)
    assert "/static/js/core/auth.js" in locales
    for ruta in locales:
        assert (WEB / ruta.removeprefix("/")).is_file(), ruta


def test_la_capa_movil_cubre_lo_que_desbordaba() -> None:
    """Cabecera, tablas como tarjetas y botones táctiles de 44 px (ver frontend/static/css/layout/mobile-layer.css)."""
    assert "CAPA MÓVIL" in CSS
    for regla in (".brand-copy { display: none; }", "table.data-table td::before", "--tap: 44px"):
        assert regla in CSS, regla


def test_la_pestana_se_llama_confort() -> None:
    """Clima, asientos, volante y lunas: «Clima» se quedaba corto. Los ids internos siguen siendo `climate`."""
    texto = visible_html()
    assert texto.count("<span>Confort</span>") == 2 and "<span>Clima</span>" not in texto    # desktop sidebar + touch dock
    for ident in ("comfortDefrost", "comfortSeats", "btnClimatePowerToggle"):
        assert f'id="{ident}"' in texto, ident
    assert "window._driverSeatHeaterOn" not in APP_JS, "el estado de los botones sale del coche, no de banderas locales"


def test_las_cabeceras_llevan_una_imagen_pequena() -> None:
    """Franja panorámica de ~100 px con una imagen ligera (las fotos originales pesan ~800 KB y empujaban el contenido)."""
    texto = visible_html()
    assert "native-hero-banner" not in texto and "native-hero-banner" not in CSS
    # The banners used to carry `style="--banner: url(...)"`; now it is a generated utility class (tools/inline_styles.py)
    imagenes = re.findall(r"--banner: url\('(/static/images/[^']+)'\)", CSS)
    assert len(imagenes) == 3 and texto.count("page-banner u---banner-url-") == 3
    for ruta in imagenes:
        fichero = WEB / ruta.removeprefix("/")
        assert fichero.is_file() and fichero.stat().st_size < 100_000, f"{ruta} pesa demasiado para una franja"
    assert re.search(r"\.page-banner \{[^}]*min-height: 9\d?px", CSS)
    for ident in ("vehicleName", "vehicleStatusBadge", "vehicleVin", "heroPlateBadge", "energyHeroTitle",
                  "chargeHeroStateBadge", "energyHeroPlateBadge", "climateHeroTitle", "climateStatusBadge"):
        assert f'id="{ident}"' in texto, ident


def test_ventilar_ya_no_esta_en_la_portada_porque_esta_en_ventanillas() -> None:
    assert "toggleVentWindows" not in visible_html() and "toggleVentWindows" not in APP_JS
    assert "ventilar_ventanillas" in APP_JS            # sigue disponible en la hoja de Ventanillas


def test_las_fichas_de_la_portada_cambian_de_icono_y_de_color() -> None:
    home = (WEB / "static" / "js" / "features" / "home.js").read_text(encoding="utf-8")
    for estado in ("closed", "open", "unknown"):
        assert f'data-state="{estado}"]' in CSS or f'[data-state="{estado}"] .card-icon-wrap' in CSS
    for ficha in ("lock", "trunk", "windows", "sunroof"):
        assert re.search(rf"{ficha}: \{{[^}}]*closed:[^}}]*open:", home, flags=re.S), ficha
    assert ".card-icon-wrap" in CSS and "background" not in re.search(
        r'\.native-card-btn\[data-state="open"\] \.card-icon-wrap \{[^}]*\}', CSS).group(0), "el color va en el icono, no en el botón"


def test_los_preajustes_de_temperatura_son_los_limites_del_climatizador() -> None:
    texto = visible_html()
    assert "comfortPreset(16)" in texto and "comfortPreset(30)" in texto and "comfortPreset(22)" in texto
    assert 'id="climateSlider" min="16" max="30"' in texto        # 16 y 30 son los límites del slider


def test_los_interruptores_de_portada_y_energia_usan_el_estado_real() -> None:
    texto = visible_html()
    assert 'data-comfort-id="alarm"' in texto
    assert 'data-comfort-id="charge"' not in texto and 'data-comfort-id="schedule"' not in texto
    for bandera in ("window._alarmOn", "window._chargeScheduleOn", "toggleAlarmState", "toggleChargeScheduleAction"):
        assert bandera not in APP_JS and bandera not in texto, bandera


def test_el_estado_desconocido_no_se_presenta_como_cerrado() -> None:
    assert "Sin datos" in APP_JS and "openClosedText" in APP_JS
    assert APP_JS.count("function toggleLockState") == 1, "toggleLockState estaba definida dos veces (ganaba la última)"


def test_app_js_no_pierde_funciones_clave() -> None:
    """Un recorte mal hecho llegó a borrar showToast y las hojas de acciones: cada función de la lista debe existir una vez."""
    for nombre in ("showToast", "openActionSheet", "closeActionSheet", "openTrunkActionSheet", "openWindowsActionSheet",
                   "openSunroofActionSheet", "executeCommandDirect", "executePendingCommand", "applyTelemetry"):
        assert len(re.findall(rf"^(?:async )?function {nombre}\b", APP_JS, flags=re.M)) == 1, nombre


def test_el_control_de_carga_usa_el_servidor_y_no_hay_limitador_de_juguete() -> None:
    """Parar/iniciar/programar/objetivo van al gestor de carga del servidor; el «Limitar carga» antiguo solo mostraba un aviso."""
    texto = visible_html()
    for ident in ("energyTargetSlider", "btnChargeStart", "btnChargeStop", "scheduleForm", "schedStart", "schedEnd",
                  "schedHours", "chargeStatusLine", "scheduleStatus", "scheduleBadge"):
        assert f'id="{ident}"' in texto, ident
    assert "chargeLimitSlider" not in texto and "setChargeLimit" not in APP_JS and "accChargeLimit" not in texto
    charge_js = (WEB / "static" / "js" / "features" / "charge-control.js").read_text(encoding="utf-8")
    for ruta in ("'target'", "'start'", "'stop'", "'schedule'"):
        assert ruta in charge_js, ruta
    assert "carga_iniciar" not in charge_js, "Ebro no acepta arrancar/parar la carga (A00084)"


def test_todo_script_del_disco_se_carga_y_todo_script_cargado_existe() -> None:
    """Un fichero nuevo en js/ que se olvida en index.html no se ejecutaría (y sin avisar): se vigila en los dos sentidos."""
    cargados = {r for r in re.findall(r'<script src="(/static/js/[^"?]+)', HTML)}
    en_disco = {"/" + f.relative_to(WEB).as_posix() for f in (WEB / "static" / "js").rglob("*.js")}
    assert cargados == en_disco, (en_disco - cargados, cargados - en_disco)
    sw = (WEB / "static" / "sw.js").read_text(encoding="utf-8")
    assert {r for r in re.findall(r"'(/static/js/[^']+)'", sw)} == en_disco, "el precaché del SW no coincide con js/"


def test_la_version_de_los_assets_la_calcula_el_servidor() -> None:
    """`?v=` sale del hash del contenido (jinja `asset_version`), no de números escritos a mano."""
    assert "{{ asset_version }}" in HTML
    assert not re.findall(r'\?v=\d+\.\d+', HTML), "quedan versiones escritas a mano en el HTML"


def test_toda_hoja_de_estilos_del_disco_esta_enlazada_y_precacheada() -> None:
    enlazadas = set(re.findall(r'href="(/static/css/[^"?]+)', HTML))
    en_disco = {"/" + f.relative_to(WEB).as_posix() for f in (WEB / "static" / "css").rglob("*.css")}
    assert enlazadas == en_disco, (en_disco - enlazadas, enlazadas - en_disco)
    sw = (WEB / "static" / "sw.js").read_text(encoding="utf-8")
    assert set(re.findall(r"'(/static/css/[^']+)'", sw)) == en_disco


def test_la_interfaz_no_usa_emojis() -> None:
    """Decisión del dueño (2026-10-28): nada de emojis en los textos; donde un icono ayuda, SVG (`core/icons.js`).
    `core/icons.js` los nombra con códigos de escape (para quitarlos de los mensajes del servidor), no literales."""
    import re
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-✒✔✖✘-➿⭐⬆⬇⌀-⏿]")
    ficheros = [WEB / "templates" / "index.html", *(WEB / "static").rglob("*.js"), *(WEB / "static").rglob("*.css")]
    ficheros.append(SRC / "services" / "notification_service.py")
    sucios = {f.name: sorted(set(emoji.findall(f.read_text(encoding="utf-8")))) for f in ficheros}
    sucios = {k: v for k, v in sucios.items() if v}
    assert not sucios, sucios


def test_no_hay_estilos_en_linea_en_la_interfaz() -> None:
    """La CSP de la página no admite `style="…"` (ni estáticos ni en plantillas JS): tools/inline_styles.py los convierte en clases y los
    dinámicos van en `data-css` + applyDataStyles() (CSSOM). `--check` de la herramienta debe seguir en verde."""
    static_style = re.compile(r'\sstyle="([^"${}]*)"')
    offenders = [str(p.relative_to(FRONTEND)) for p in [FRONTEND / "templates" / "index.html", *(FRONTEND / "static" / "js").rglob("*.js")]
                 if static_style.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"style=\"…\" en línea en {offenders}: ejecuta python tools/inline_styles.py --apply"
    assert "style=\"" not in visible_html() and "setAttribute('style'" not in APP_JS
    assert "utilities.css" in HTML and "applyDataStyles" in APP_JS


def test_the_app_never_uses_the_browser_dialogs() -> None:
    """alert/confirm/prompt look foreign on the phone and block the page: the app uses its own dialog (core/feedback.js) or a toast."""
    import re

    files = list((WEB / "static" / "js").rglob("*.js")) + list((WEB / "admin" / "assets").glob("*.js"))
    offenders = [f.name for f in sorted(files) if re.search(r"(?<![.\w])(alert|confirm|prompt)\(", f.read_text(encoding="utf-8"))]
    assert not offenders, f"diálogos del navegador en {offenders}: usa confirmDialog/promptDialog/alertDialog o showToast (ask() en la consola)"
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
    assert not missing, f"Manejadores declarativos sin función en el JavaScript: {missing}"
