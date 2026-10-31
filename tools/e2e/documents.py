"""Documentos del coche en la ficha: factura de compra, póliza del seguro (varios con título) y otros. Ver tools/e2e/README.md."""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

BASE = os.environ.get("EVBRO_URL", "http://127.0.0.1:8099")
USER = os.environ.get("EVBRO_USER", "juan")
PASSWORD = os.environ.get("EVBRO_PASSWORD", "una-contraseña-larga-1")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="evbro-e2e-")
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
fails = []


def ok(c, m):
    print(("  ✔ " if c else "  ✘ ") + m)
    if not c:
        fails.append(m)


def pdf(name):
    return {"name": name, "mimeType": "application/pdf", "buffer": PDF}


with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("dark", "light"):
        # bypass_csp: `wait_for_function` evaluates a string in the page, which the app's CSP forbids (csp.py checks the policy itself)
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="es-ES", bypass_csp=True)
        ctx.request.post(f"{BASE}/api/auth/login", headers={"Origin": BASE}, data={"username": USER, "password": PASSWORD})
        ctx.add_init_script(f"localStorage.setItem('evbro_theme','{theme}')")
        ctx.add_init_script("new MutationObserver(() => { const b = document.querySelector('#appDialog.open #appDialogOk'); if (b) b.click(); }).observe(document, { attributes: true, subtree: true, attributeFilter: ['class'] })")   # the app's own confirm dialog is accepted
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: (errs.append("diálogo del navegador: " + d.message), d.dismiss()))
        storage_requests = [0]
        pg.on("request", lambda r: storage_requests.__setitem__(0, storage_requests[0] + 1) if r.url.endswith("/api/account/storage") else None)
        pg.goto(BASE)
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        tag = theme
        vin = pg.evaluate("currentVin")

        # start clean: remove what a previous run left
        for d in ctx.request.get(f"{BASE}/api/vehicles/{vin}/documents").json():
            ctx.request.delete(f"{BASE}{d['url']}", headers={"Origin": BASE})

        pg.evaluate("openVehicleSettings()")
        pg.wait_for_timeout(500)
        pg.click("button:text-is(\"Desplegar todo\")")
        pg.wait_for_timeout(300)
        ok(pg.text_content("#vdocListPurchase").strip() == "Sin factura todavía.", f"{tag}: sin documentos, la factura dice «Sin factura todavía.»")
        ok(pg.is_visible("button:text-is(\"Añadir factura\")"), f"{tag}: el dueño ve «Añadir factura»")
        pg.wait_for_function("!document.getElementById('vdocStorage').hidden")
        storage0 = ctx.request.get(f"{BASE}/api/account/storage").json()
        line0 = pg.text_content("#vdocStorage")
        ok(line0.startswith("Documentos, facturas y fotos: ") and ("sin límite" in line0 or f"de {storage0['limit_mb']} MB" in line0.replace(".", "")),
           f"{tag}: se ve el espacio usado ({line0})")

        # purchase invoice
        storage_requests[0] = 0
        pg.set_input_files("#vdocFilePurchase", pdf("Factura Ebro.pdf"))
        pg.wait_for_selector("#vdocListPurchase .vdoc-item")
        ok(pg.text_content("#vdocListPurchase .vdoc-name") == "Factura de compra", f"{tag}: la factura aparece como «Factura de compra»")
        storage1 = ctx.request.get(f"{BASE}/api/account/storage").json()
        ok(storage1["used_bytes"] == storage0["used_bytes"] + len(PDF), f"{tag}: la subida cuenta en el espacio ({storage0['used_bytes']} -> {storage1['used_bytes']})")
        ok(storage_requests[0] >= 1, f"{tag}: tras subir se vuelve a pedir el espacio usado")
        meta = pg.text_content("#vdocListPurchase .vdoc-meta")
        ok(meta.startswith("PDF · ") and "KB" in meta, f"{tag}: tipo, tamaño y fecha ({meta})")

        # two policy documents with titles
        for title in ("Condiciones generales", "Condiciones particulares"):
            pg.fill("#vdocPolicyTitle", title)
            pg.set_input_files("#vdocFilePolicy", pdf("poliza.pdf"))
            pg.wait_for_function(f"document.querySelectorAll('#vdocListPolicy .vdoc-item').length >= {1 if title.endswith('generales') else 2}")
        names = sorted(pg.eval_on_selector_all("#vdocListPolicy .vdoc-name", "e=>e.map(x=>x.textContent)"))
        ok(names == ["Condiciones generales", "Condiciones particulares"], f"{tag}: dos documentos de la póliza con su título ({names})")
        ok(pg.input_value("#vdocPolicyTitle") == "", f"{tag}: el título se vacía tras subir")
        suggestions = pg.eval_on_selector_all("#vdocPolicyTitles option", "e=>e.map(o=>o.value)")
        ok("Condiciones generales" in suggestions and "Condiciones particulares" in suggestions, f"{tag}: títulos sugeridos")

        # another document (technical sheet)
        pg.select_option("#vdocOtherKind", "technical_sheet")
        pg.set_input_files("#vdocFileOther", pdf("ficha.pdf"))
        pg.wait_for_selector("#vdocListOther .vdoc-item")
        ok(pg.text_content("#vdocListOther .vdoc-name") == "Ficha técnica" and pg.text_content("#vdocListOther .vdoc-meta").startswith("PDF"), f"{tag}: «Otros» usa el tipo como nombre del documento")

        # a non-accepted file is refused with a message
        pg.set_input_files("#vdocFileOther", {"name": "virus.pdf", "mimeType": "application/pdf", "buffer": b"MZ\x90\x00not a pdf"})
        pg.wait_for_timeout(800)
        ok(pg.eval_on_selector_all("#vdocListOther .vdoc-item", "e=>e.length") == 1, f"{tag}: un fichero que no es PDF ni imagen no se guarda")

        # opening: the link points to the download URL, which answers the PDF inline
        href = pg.get_attribute("#vdocListPurchase .vdoc-open", "href")
        ok(pg.get_attribute("#vdocListPurchase .vdoc-open", "target") == "_blank", f"{tag}: se abre en otra pestaña")
        r = ctx.request.get(f"{BASE}{href}")
        ok(r.status == 200 and r.headers.get("content-type") == "application/pdf" and r.body() == PDF, f"{tag}: la descarga responde 200 con application/pdf")
        ok(r.headers.get("x-content-type-options") == "nosniff" and r.headers.get("content-disposition", "").startswith("inline"), f"{tag}: inline y nosniff")

        # sizes on a phone: nothing overflows the card
        over = pg.evaluate("""() => [...document.querySelectorAll('.vdoc-item, .vdoc-add, .vdoc-form, #vdocStorage')].filter(e => e.offsetParent)
            .filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1).length""")
        ok(over == 0, f"{tag}: nada se sale a 390 px")
        small = pg.evaluate("""() => [...document.querySelectorAll('.vdoc-del, .vdoc-add')].filter(e => e.offsetParent)
            .filter(e => e.getBoundingClientRect().height < 44).length""")
        ok(small == 0, f"{tag}: botones de al menos 44 px")
        pg.locator(".vp-section[data-key='docs']").scroll_into_view_if_needed()
        pg.screenshot(path=f"{OUT}/documents-{theme}.png", full_page=False)

        # delete one policy document (confirmation accepted): the space line is refreshed from the server
        before = ctx.request.get(f"{BASE}/api/account/storage").json()["used_bytes"]
        storage_requests[0] = 0
        pg.click("#vdocListPolicy .vdoc-item:first-child .vdoc-del")
        pg.wait_for_function("document.querySelectorAll('#vdocListPolicy .vdoc-item').length === 1")
        pg.wait_for_timeout(400)
        after = ctx.request.get(f"{BASE}/api/account/storage").json()["used_bytes"]
        ok(after == before - len(PDF) and storage_requests[0] > 0, f"{tag}: borrar libera espacio y la línea se vuelve a pedir ({before} -> {after})")
        ok(len([d for d in ctx.request.get(f"{BASE}/api/vehicles/{vin}/documents").json() if d["kind"] == "insurance_policy"]) == 1,
           f"{tag}: borrar quita el documento")
        ok(ctx.request.get(f"{BASE}{href}").status == 200, f"{tag}: los demás siguen")

        ok(not errs, f"{tag}: sin errores de JavaScript ({errs[:2]})")
        for d in ctx.request.get(f"{BASE}/api/vehicles/{vin}/documents").json():
            ctx.request.delete(f"{BASE}{d['url']}", headers={"Origin": BASE})
        ctx.close()
    b.close()

print(f"Capturas en {OUT}")
print("FALLOS: " + ("ninguno" if not fails else "; ".join(fails)))
sys.exit(1 if fails else 0)
