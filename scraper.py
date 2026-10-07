"""
scraper.py: lee los cafés de cada tostador de lista_tostadores.csv y genera cafes.json,
estado.json e informe.csv (qué ha pasado con cada tostador).
Orden de métodos por tostador: Shopify -> WooCommerce -> HTML genérico (PrestaShop y otros).
Columnas opcionales en el CSV: Affiliate_Template (enlace de red de afiliación con {url}), URL_Catalogo (página de listado de cafés, recomendada para
tiendas que no son Shopify/Woo), Envio_Gratis, Pedido_Minimo.
"""
import csv, json, os, re, threading, time, unicodedata, urllib.parse as up
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import requests
from bs4 import BeautifulSoup
import warnings
from html import unescape
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError: pass

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "Accept-Language": "es-ES,es;q=0.9,en;q=0.8", "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
RECHAZ = []
LOC = threading.local()          # rechazos por tostador (cada hilo lleva los suyos)
VERBOSO = False
def log(*a):
    if VERBOSO: print(*a, flush=True)
def agotado(t): return time.time() > t.get("limite", 1e18)   # plazo máximo por tostador
ORIG = ["Etiopía","Kenia","Colombia","Brasil","Guatemala","Costa Rica","Perú","Honduras","El Salvador","Panamá",
        "Ruanda","Burundi","Uganda","Venezuela","Nicaragua","México","Indonesia","Tanzania","Bolivia","Ecuador","Yemen","India","Papúa Nueva Guinea","Tailandia","Jamaica","Vietnam","Laos","Malawi","Zambia","República Dominicana","Hawái","Congo","Filipinas","Timor Oriental","Myanmar"]
PROC = {"Natural":["natural"],"Lavado":["lavado","washed"],"Honey":["honey"],"Anaeróbico":["anaer"]}
NOTAS = {"Frutos rojos":["frutos rojos","frutas rojas","fresa","frambuesa","arándano","grosella","cereza","red fruit","berry","strawberry","raspberry","blueberry","cherry"],
 "Cítrico":["cítric","naranja","limón","lime","mandarina","bergamota","pomelo","citrus","orange","lemon","grapefruit","tangerine"],
 "Fruta tropical":["tropical","mango","piña","maracuyá","maracuya","papaya","lichi","guayaba","pineapple","passion fruit"],
 "Fruta de hueso":["melocotón","albaricoque","ciruela","nectarina","peach","apricot","plum"],
 "Manzana y pera":["manzana","pera","apple","pear"],"Uva y vino":["uva","vino","pasas","wine","grape","raisin"],
 "Floral":["floral","jazmín","flores","azahar","lavanda","jasmine","flower"],
 "Té":["té negro","té verde","black tea","earl grey","chai"],"Chocolate":["chocolate","brownie"],"Cacao":["cacao","cocoa","nibs"],
 "Caramelo":["caramelo","toffee","azúcar moreno","caramel"],"Miel":["miel","honey"],"Panela":["panela","melaza","molasses"],
 "Frutos secos":["frutos secos","almendra","avellana","nuez","cacahuete","nuts","almond","hazelnut","walnut","peanut"],
 "Vainilla":["vainilla","vanilla"],"Especias":["especias","canela","clavo","jengibre","cardamomo","pimienta","spice","cinnamon"],
 "Herbal":["herbal","hierba","menta","eucalipto","mint"],"Madera y tabaco":["madera","tabaco","cedro","tobacco","cedar"],
 "Mermelada":["mermelada","compota","confit"],"Cereal y pan":["cereal","pan tostado","galleta","bizcocho","biscuit","bread","malta","malt"]}
METODOS = {"Espresso":["espresso","expreso"],"Filtro":["filtro","v60","chemex","kalita","goteo","pour over","filter"],
 "Moka":["moka","italiana"],"Prensa francesa":["prensa francesa","french press","émbolo","embolo"],"AeroPress":["aeropress"],
 "Cold brew":["cold brew","cold-brew","coldbrew","infusión en frío"],
 "Superautomática":["superautomática","superautomatica","superautomáticas","bean to cup","máquinas automáticas"]}
NO = ["taza","merch","camiseta","molinillo","cafetera","papel","v60","aeropress","gift","regalo","tote","curso",
      "suscrip","pack","prensa","tetera","báscula","bascula","jarra","cápsula","capsula","sudadera"]

def antibot(r):
    h = {k.lower(): str(v).lower() for k, v in r.headers.items()}
    cuerpo = r.text[:5000].lower()
    cf = "cloudflare" in h.get("server", "") or "cf-ray" in h or h.get("cf-mitigated") == "challenge"
    otro = "x-sucuri-id" in h or "x-amzn-waf-action" in h or "x-datadome" in h or "captcha" in cuerpo[:3000]
    reto = "just a moment" in cuerpo or "challenge-platform" in cuerpo or "attention required" in cuerpo
    if (cf and (r.status_code in (202, 403, 429, 503) or reto)) or reto or (otro and r.status_code in (202, 403, 429, 503)):
        return "Cloudflare" if cf else "antibot"
    return ""

def get(u, j=False):
    log('  GET', u)
    for i in range(3):
        r = requests.get(u, headers=H, timeout=20)
        bloqueo = antibot(r)
        if not bloqueo and j and r.status_code == 202: bloqueo = "antibot"
        if bloqueo: raise Exception(f"BLOQUEADO por {bloqueo} (HTTP {r.status_code}): la web no admite lectura automática")
        if r.status_code in (429, 500, 502, 503) and i < 2: time.sleep(3 * (i + 1)); continue
        r.raise_for_status(); return r.json() if j else r.text

def leer_csv(p="lista_tostadores.csv"):
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):          # Excel en España guarda en cp1252
        try: txt = raw.decode(enc); break
        except UnicodeDecodeError: pass
    lin = txt.splitlines()
    d = ";" if lin[0].count(";") >= lin[0].count(",") else ","
    out = []
    for r in csv.DictReader(lin, delimiter=d):
        n, w = (r.get("Tostador") or "").strip(), (r.get("URL") or "").strip()
        if n and w:
            pu = up.urlparse(w)
            out.append({"nombre": n, "web": w.rstrip("/"), "org": f"{pu.scheme}://{pu.netloc}",
                "ciudad": (r.get("Ciudad") or "").strip(), "cat": (r.get("URL_Catalogo") or "").strip(),
                "sh": r.get("Envio_Gratis") or "", "mn": r.get("Pedido_Minimo") or "",
                "param": (r.get("Affiliate_Param") or "").strip() or "ref", "code": (r.get("Affiliate_Code") or "").strip(),
                "aff": (r.get("Affiliate_URL") or "").strip(), "tpl": (r.get("Affiliate_Template") or "").strip(),
                "excl": [w.strip().lower() for w in (r.get("Excluir") or "").split(",") if w.strip()]})
    return out

def num(v):
    try: return float(str(v).replace(",", "."))
    except Exception: return None

def no_cafe(s): return any(x in s.lower() for x in NO)
NEG = re.compile(r"\b(?:molinill|grinder|m[aá]quina|cafeter|accesorio|merch|camiseta|gorra|sudadera|tote|regalo|gift|tarjeta|curso|formaci|taller|suscrip|pack|kit\b|b[aá]scula|balanza|jarra|tetera|hervidor|limpi|descalcific|tamper|portafiltro|dripper|chemex|aeropress|c[aá]psula|taza(?! de excelencia)|vaso|termo|botella|bote\b|prueba|test\b|comprobaci|tools|equipamiento|herramienta|libro|e-?book|delantal|pegatina|p[oó]ster|mug\b|filtros? de papel|papel|nuez|nueces|pipas|pasas|cacahuete|almendra|avellana|pistacho|anacardo|d[aá]til|higo|orejones|frutos secos|snack|galleta|turr[oó]n|aceite|cerveza|infusi|tisana|rooibos|matcha|chai|t[eé] (?:verde|negro|rojo|blanco)|vino|licor|tableta|bomb[oó]n|bizcocho|leche|condensad|dulce de|dulces\b|crema de|sirope|jarabe|helado|cookie|soluble|salsa|mermelada|chocolate|cacao|caf[eé] verde|green coffee|sin tostar|bono|entrada|experiencia|hario|kalita|fellow|baratza|comandante|timemore|bialetti|marzocco|cafec|origami|kinto|stanley|espro|acaia|moccamaster|sage\b|orea|subminimal|descatalogado|env[ií]os?|tarifa|plan renove|combo|bundle|box\b|sourdough|^\s*v60\s*$|^\s*molino\b|tostadora|prensa\b|cuchara|agitad|exfoliante|jab[oó]n|hojicha|sencha|genmaicha|oolong|pu-?erh|yerba|^\s*(?:az[uú]car|panela)\b|mascobado|review|opini[oó]n|cecotec|delonghi|de'longhi|krups|philips|nespresso|dolce gusto|recipiente|cristal|tapa\b|vidrio|subscription|b2b|wholesale|mayorista|kruve|trinity)", re.I)
NEG_URL = re.compile(r"\b(?:molinill|m[aá]quina|cafeter|accesorio|camiseta|gorra|tote|regalo|gift|curso|taller|suscrip|pack|capsula|c[aá]psula|taza(?!-de-excelencia)|termo|dripper|aeropress|chemex|filtros?-de-papel|molino|tostadora)", re.I)
NEG_CAT = re.compile(r"\b(?:accesorios?|merch|cafeteras?|molinillos?|m[aá]quinas?|cursos?|talleres?|tazas?|regalos?|packs?|suscripci|c[aá]psulas?|infusion|snacks?|frutos secos|libros?|ropa|equipamiento|herramientas|bebidas|dulces|reposter)", re.I)
VARIEDAD = re.compile(r"geisha|gesha|bourbon|borb[oó]n|caturra|catua[ií]|typica|pacamara|sidra|sl-?28|sl-?34|castillo|heirloom|ruiru|batian|catimor|wush|laurina|pacas|villa sarchi|obata|maragogipe|mundo novo|peaberry|supremo|excelso|honey|lavado|washed|anaer|\blote\b|\bAA\b|\bAB\b", re.I)
POS = re.compile(r"caf[eé]\b|coffee|grano|espresso|origen|tueste|blend|especialidad|single|microlote|descafe|decaf", re.I)

CAFE_TXT = re.compile(r"tueste|ar[aá]bica|espresso|molido|\bgranos?\b|cafetera|altitud|\bcata\b|finca|caf[eé] de especialidad", re.I)

def es_cafe(nombre, cab, desc, tiene_peso, precio, confiado=False, ev_extra=False):
    """True = café; False = descartar. Si es dudoso (sin pruebas claras) devuelve True y marca LOC.dud;
    luego se decide por el rango de precios de los cafés seguros de ese tostador."""
    LOC.dud = False
    def no(m): getattr(LOC, "rech", RECHAZ).append(f"{nombre} [{m}]"); return False
    if precio is None or precio <= 0: return no("sin precio")
    if NEG.search(nombre): return no("accesorio/alimento/merch")
    if NEG_CAT.search(cab) and not POS.search(f"{nombre} {cab}"): return no("categoría que no es café")
    if precio > 60 and not tiene_peso: return no("precio alto sin gramos")
    if confiado or ev_extra or POS.search(f"{nombre} {cab}"): return True
    if pais(nombre, "") or VARIEDAD.search(nombre): return True      # "Colombia Huila", "Kiringa AA", "Geisha"
    ev = CAFE_TXT.search(desc) or (pais(nombre, desc) and re.search(r"notas|tueste|cata", desc, re.I))
    if ev and (tiene_peso or precio < 40): return True
    LOC.dud = True
    return True

def filtrar_dudosos(r):
    fuertes = [c for c in r if not c.get("_d")]
    out = []
    if len(fuertes) >= 2:
        pr = sorted(c["pr"] for c in fuertes); lo, hi = pr[0], pr[-1]
        for c in r:
            if not c.get("_d") or lo * 0.5 <= c["pr"] <= hi * 1.5: out.append(c)
            else: getattr(LOC, "rech", RECHAZ).append(f"{c['n']} [dudoso: precio fuera del rango de sus cafés]")
    else: out = [c for c in r if not c.get("_d")]
    for c in out: c.pop("_d", None)
    return out

def gramos(s):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?(kg|kilos?|gramos?|grs?|gms?|g)\b", s or "", re.I)
    if not m: return None
    v = float(m.group(1).replace(",", ".")) * (1000 if m.group(2).lower().startswith("k") else 1)
    return int(v) if 100 <= v <= 5000 else None
def hallar(d, t): return [k for k, v in d.items() if any(re.search(r"\b" + re.escape(x) + r"(?:s|es|os|as|a|o)?\b", t, re.I) for x in v)]
def sca(t):
    m = re.search(r"(?:sca|puntuaci[oó]n|score)\D{0,12}(8\d(?:[.,]\d{1,2})?|9[0-5](?:[.,]\d{1,2})?)|(8\d(?:[.,]\d{1,2})?|9[0-5](?:[.,]\d{1,2})?)\s*(?:pts|puntos|points|sca)\b", t, re.I)
    return float((m.group(1) or m.group(2)).replace(",", ".")) if m else None
ALIAS = {"Etiopía": ["ethiopia", "guji", "yirgacheffe", "sidamo", "sidama", "harrar", "limu", "jimma"],
 "Kenia": ["kenya", "nyeri", "kiambu", "kirinyaga", "muranga"], "Colombia": ["huila", "narino", "cauca", "tolima", "quindio", "antioquia"],
 "Brasil": ["brazil", "cerrado", "minas gerais", "mogiana"], "Guatemala": ["huehuetenango", "atitlan"], "Costa Rica": ["tarrazu"],
 "Perú": ["peru", "cajamarca", "chanchamayo", "cusco"], "Honduras": ["marcala", "copan"], "Panamá": ["panama", "boquete"],
 "Ruanda": ["rwanda"], "Nicaragua": ["jinotega", "matagalpa"], "El Salvador": ["santa ana", "apaneca"], "México": ["mexico", "chiapas", "oaxaca"],
 "Indonesia": ["sumatra", "sulawesi", "java"], "Bolivia": ["caranavi"], "India": ["malabar"], "Papúa Nueva Guinea": ["papua", "papua new guinea", "png"], "Tailandia": ["thailand"], "Hawái": ["hawaii", "kona"], "República Dominicana": ["dominicana"], "Filipinas": ["philippines"], "Timor Oriental": ["timor"]}
def sa(x): return "".join(c for c in unicodedata.normalize("NFD", (x or "").lower()) if unicodedata.category(c) != "Mn")
def pais(titulo, txt):
    for src in (titulo, txt):
        e = sa(src)
        for p in ORIG:
            if any(re.search(r"\b" + re.escape(c) + r"\b", e) for c in [sa(p)] + ALIAS.get(p, [])): return p
    return "Blend" if "blend" in titulo.lower() else ""

def afiliado(u, t):
    if t["aff"]: return t["aff"]
    if t["tpl"]: return t["tpl"].replace("{url}", up.quote(u, safe=""))   # plantilla de red de afiliación
    q = up.parse_qs(up.urlparse(u).query)
    if t["code"]: q[t["param"]] = [t["code"]]
    q["utm_source"] = ["granos"]
    return up.urlunparse(up.urlparse(u)._replace(query=up.urlencode(q, doseq=True)))

PESOS = [100, 125, 150, 200, 227, 250, 340, 350, 400, 454, 500, 750, 1000, 2000, 2500, 5000]

def peso_envio(gr):
    """Peso de envío de la tienda -> formato estándar (el envío incluye el envase, de ahí la tolerancia)."""
    gr = num(gr)
    if not gr or gr < 5: return None
    if gr < 100: return int(gr)
    mejor = min(PESOS, key=lambda p: abs(p - gr))
    return mejor if abs(mejor - gr) / mejor <= 0.2 else None

def gramos_texto(txt):
    m = re.search(r"(?:formato|peso|contenido|cantidad|neto)\D{0,15}(\d+(?:[.,]\d+)?\s?(?:kg|kilos?|gramos|gr|g)\b)", txt or "", re.I)
    return gramos(m.group(1)) if m else None

SOLO = re.compile(r"\b(?:bags?|bolsitas?|sobres?|sachets?|dip|drip|immersion|inmersi[oó]n|monodosis|stick)\b", re.I)
STD = {100, 125, 150, 200, 227, 250, 340, 350, 400, 454, 500, 1000}

def gramos_estandar(txt):
    """Último recurso: primer formato 'estándar' que aparezca en el texto (ignora recetas tipo 18 g)."""
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s?(kg|kilos?|gramos?|grs?|gms?|g)\b", txt or "", re.I):
        v = float(m.group(1).replace(",", ".")) * (1000 if m.group(2).lower().startswith("k") else 1)
        if int(v) in STD: return int(v)
    return None

def ficha(t, n, u, img, txt, precio, stock, g=None, gsrc=""):
    pr = round(precio, 2)
    if g and SOLO.search(n) and gsrc not in ("título", "variante", "descripción", "manual"): g = None   # monodosis: el peso de envío no vale
    tl = txt.lower()
    return {"n": norm(n), "r": t["nombre"], "c": t["ciudad"], "u": u, "aff_url": afiliado(u, t), "img": img or "",
        "mt": (lambda m: [] if len(m) >= 4 else m)(hallar(METODOS, txt)),
        "o": pais(n, txt), "p": (hallar(PROC, txt) or [""])[0], "nt": hallar(NOTAS, txt), "pr": pr, "g": g, "gsrc": gsrc if g else "", "p250": round(precio * 250 / g, 2) if g else None,
        "af": bool(t["aff"] or t["tpl"] or t["code"]), "s": bool(stock),
        "sca": sca(txt), "rt": None, "sh": num(t["sh"]), "mn": num(t["mn"]) or 0, "_d": getattr(LOC, "dud", False)}

def norm(s): return re.sub(r"\s+", " ", unescape(s or "")).strip()
def texto(h): return norm(BeautifulSoup(h or "", "html.parser").get_text(" "))

def shopify(t):
    out, ruta = [], "/products.json"
    for pg in range(1, 11):
        if agotado(t): break
        try: d = get(f"{t['org']}{ruta}?limit=250&page={pg}", True)["products"]
        except Exception:
            if pg > 1 or ruta != "/products.json": raise
            ruta = "/collections/all/products.json"; d = get(f"{t['org']}{ruta}?limit=250&page={pg}", True)["products"]
        if not d: break
        for p in d:
            tags = p.get("tags") or []; tags = tags.split(",") if isinstance(tags, str) else tags
            cab = f"{p['title']} {p.get('product_type','')} {' '.join(tags)}"
            vs = p.get("variants") or []
            pesos = any(gramos(v.get("title", "")) for v in vs)
            mejor = None
            for v in vs:
                pr = float(v["price"])
                g, src = gramos(v.get("title", "")), "variante"
                if not g: g, src = gramos(p["title"]), "título"
                if not g: g, src = gramos(" ".join(str(v.get(k) or "") for k in ("option1", "option2", "option3"))), "opciones"
                if not g: g, src = gramos_texto(texto(p.get("body_html"))), "descripción"
                if not g: g, src = peso_envio(v.get("grams")), "peso_envio"
                if not g: g, src = gramos_estandar(f"{p['title']} {texto(p.get('body_html'))}"), "texto"
                clave = abs(g - 250) if g else 99999
                if mejor is None or clave < mejor[3]: mejor = (pr, g, src, clave)
            if not mejor or not es_cafe(p["title"], f"{p.get('product_type','')} {' '.join(tags)}", texto(p.get('body_html')), pesos, mejor[0]): continue
            out.append(ficha(t, p["title"], f"{t['org']}/products/{p['handle']}", p["images"][0]["src"] if p.get("images") else "",
                f"{cab} {texto(p.get('body_html'))} {' '.join(v.get('title','') for v in vs)}", mejor[0], any(v.get("available") for v in vs), mejor[1], mejor[2]))
        time.sleep(1)
    return out

def woo_variantes(t, pid):
    """Precio y gramos de cada variación; devuelve la más cercana a 250 g."""
    mejor = None
    for v in get(f"{t['org']}/wp-json/wc/store/v1/products?type=variation&parent={pid}&per_page=100", True):
        pr = v.get("prices") or {}; c = int(pr.get("price") or 0)
        if not c: continue
        g = gramos(" ".join([str(v.get("variation") or ""), str(v.get("name") or "")] + [str(a.get("value") or "") for a in v.get("attributes", [])]))
        clave = abs(g - 250) if g else 99999
        if mejor is None or clave < mejor[2]: mejor = (c / 10 ** pr.get("currency_minor_unit", 2), g, clave)
    return mejor

def woo(t):
    out = []
    for pg in range(1, 11):
        if agotado(t): break
        d = get(f"{t['org']}/wp-json/wc/store/v1/products?per_page=100&page={pg}", True)
        if not d: break
        for p in d:
            cats = " ".join(c["name"] for c in p.get("categories", []))
            pr = p["prices"]; cents = int(pr.get("price") or 0)
            if not cents: cents = int((pr.get("price_range") or {}).get("min_amount") or 0) or int(pr.get("regular_price") or 0)
            precio = cents / 10 ** pr.get("currency_minor_unit", 2)
            attr = " ".join(x["name"] for a in p.get("attributes", []) for x in a.get("terms", []))
            gs = [gramos(x["name"]) for a in p.get("attributes", []) for x in a.get("terms", [])]
            g_attr = min([x for x in gs if x], default=None)
            w = num(p.get("weight")); g_peso = peso_envio(w * 1000 if w and w < 10 else w)
            desc = f"{texto(p.get('short_description'))} {texto(p.get('description'))}"
            g_var = None
            if p.get("type") == "variable" and not agotado(t):
                try:
                    vm = woo_variantes(t, p["id"]); time.sleep(0.3)
                    if vm: precio, g_var = vm[0], vm[1]
                except Exception: pass
            if not es_cafe(p["name"], cats, desc, bool(gramos(p["name"]) or gramos(desc) or gramos(attr)), precio): continue
            out.append(ficha(t, p["name"], p["permalink"], p["images"][0]["src"] if p.get("images") else "",
                f"{p['name']} {cats} {attr} {desc}",
                precio, p.get("is_in_stock", True),
                g_var or gramos(p["name"]) or g_attr or gramos_texto(desc) or g_peso or gramos_estandar(desc), "woo"))
        if len(d) < 100: break
        time.sleep(1)
    return out

RUTAS = ["/tienda", "/shop", "/productos", "/cafe", "/cafes", "/collections/all", "/tienda-online"]

def es_producto(path):   # PrestaShop /es/cafetazos/207-1700-x  |  Woo /producto/x  |  Shopify /products/x
    s = path.strip("/").split("/")
    if re.fullmatch(r"\d+-[\w-]+(\.html)?", s[-1]) and any(not re.fullmatch(r"[a-z]{2}", x) for x in s[:-1]): return True
    if len(s) >= 3 and s[-3] == "product": return True      # Square Online: /product/slug/ID
    return len(s) >= 2 and s[-2] in ("producto", "productos", "product", "products", "product-page", "p", "tienda", "shop")

EXCL = re.compile(r"cart|carrito|checkout|cuenta|account|login|contact|blog|politica|aviso|cookies|envios|condiciones|faq|nosotros|about|privacidad", re.I)

def enlaces_crudos(html, base, org, fn):
    """Enlaces a producto aunque estén dentro de JSON/JS (comillas escapadas)."""
    res = []
    for h in re.findall(r'href=\\?["\']([^"\'\\\s>]+)', html):
        u = up.urljoin(base, h.replace("\\/", "/")).split("#")[0].split("?")[0]; pu = up.urlparse(u)
        if dominio(pu.netloc) == dominio(up.urlparse(org).netloc) and fn(pu.path) and u not in res: res.append(u)
    return res

def enlaces_tarjeta(soup, base, org):
    """Plataformas sin patrón de URL claro: enlaces cuya 'tarjeta' (el bloque que los rodea) muestra un precio en €."""
    res = []
    for a in soup.find_all("a", href=True):
        if re.search(r'[\\"\'{}<>\s]', a["href"].strip()): continue
        u = up.urljoin(base, a["href"]).split("#")[0].split("?")[0]; pu = up.urlparse(u)
        if dominio(pu.netloc) != dominio(up.urlparse(org).netloc) or u in res or u.rstrip("/") == base.rstrip("/") or EXCL.search(pu.path): continue
        nodo = a
        for _ in range(4):
            nodo = nodo.parent
            if nodo is None: break
            txt = nodo.get_text(" ", strip=True)
            if len(txt) > 400: break
            if re.search(r"\d+[.,]\d{2}\s?€|€\s?\d+[.,]\d{2}|\d+\s?€", txt): res.append(u); break
    return res

def precio_html(soup):
    for e in soup.select(".price, .product-price, [itemprop=price], .current-price, .sqs-money-native, .woocommerce-Price-amount, .amount"):
        m = re.search(r"(\d{1,4}(?:[.,]\d{1,2})?)", e.get("content") or e.get_text(" ", strip=True))
        if m and (num(m.group(1)) or 0) > 0: return num(m.group(1))
    cuerpo = BeautifulSoup(str(soup), "html.parser")
    for e in cuerpo(["script", "style", "nav", "header", "footer", "noscript"]): e.decompose()
    m = re.search(r"(\d{1,4}(?:[.,]\d{1,2})?)\s?(?:€|eur\b)|(?:€|eur\b)\s?(\d{1,4}(?:[.,]\d{1,2})?)", cuerpo.get_text(" ", strip=True), re.I)
    v = num(m.group(1) or m.group(2)) if m else None
    if v and v > 0: return v
    for pat in (r'data-(?:product-)?price=["\']?([\d.,]+)', r'"price"\s*:\s*"?([\d]+(?:\.\d{1,2})?)', r'"amount"\s*:\s*"?([\d]+(?:\.\d{1,2})?)'):
        for m in re.finditer(pat, str(soup)):
            v = num(m.group(1))
            if v and 1 <= v <= 500: return v
    return None

def es_categoria(path):  # /es/3-cafetazos
    s = path.strip("/").split("/")
    return bool(re.fullmatch(r"\d+-[\w-]+", s[-1])) and all(re.fullmatch(r"[a-z]{2}", x) for x in s[:-1])

def dominio(h): return ".".join(h.split(".")[-2:])

def enlaces(soup, base, org, fn):
    res = []
    for a in soup.find_all("a", href=True):
        if re.search(r'[\\"\'{}<>\s]', a["href"].strip()): continue      # enlaces rotos/escapados
        u = up.urljoin(base, a["href"]).split("#")[0].split("?")[0]
        pu = up.urlparse(u)
        if dominio(pu.netloc) == dominio(up.urlparse(org).netloc) and fn(pu.path) and u not in res: res.append(u)
    return res

def limpiar_nombre(n):
    n = re.split(r"\s[—–|]\s", n)[0].strip()
    n = re.sub(r"^caf[eé]s? de especialidad\s*[:\-–]?\s*", "", n, flags=re.I).strip() or n
    return n[:1].upper() + n[1:] if n and n[0].islower() else n

def pagina(t, u, confiar=False):
    soup = BeautifulSoup(get(u), "html.parser"); prod = None
    for s in soup.find_all("script", type="application/ld+json"):
        try: d = json.loads(s.string or "")
        except Exception: continue
        for it in (d if isinstance(d, list) else d.get("@graph", [d])):
            if isinstance(it, dict) and "Product" in str(it.get("@type")): prod = it
    meta = lambda k: (soup.find("meta", property=k) or soup.find("meta", attrs={"name": k}) or {}).get("content", "")
    n = norm((prod or {}).get("name") or meta("og:title") or (soup.h1.get_text() if soup.h1 else ""))
    of = (prod or {}).get("offers") or {}
    of = of[0] if isinstance(of, list) and of else of if isinstance(of, dict) else {}
    pr = num(of.get("price") or of.get("lowPrice") or meta("product:price:amount"))
    if pr is None: pr = precio_html(soup)
    n_raw, n = n, limpiar_nombre(n)
    if not n or pr is None: return None
    img = (prod or {}).get("image") or meta("og:image")
    img = img[0] if isinstance(img, list) and img else img.get("url", "") if isinstance(img, dict) else img
    desc = " ".join(e.get_text(" ") for e in soup.select("[itemprop=description], .product-description, #description, .product-information"))
    txt = f"{n_raw} {(prod or {}).get('description','')} {desc} {meta('og:description')}"
    ev_extra = len(re.findall(r"altitud|msnm|masl|tueste|variedad|notas? de cata|proceso|finca|cosecha", soup.get_text(" ", strip=True), re.I)) >= 2
    if not es_cafe(n_raw, up.urlparse(u).path, f"{(prod or {}).get('description','')} {desc} {meta('og:description')}", bool(gramos(n) or gramos(desc)), pr, confiar, ev_extra): return None
    stock = "OutOfStock" not in str(of.get("availability", ""))
    return ficha(t, n, u, img if isinstance(img, str) else "", txt, pr, stock, gramos(n) or gramos_texto(desc), "página")

def html_generico(t):
    confiar = bool(t["cat"])
    if t["cat"]: listados = [t["cat"]]
    else:
        home = BeautifulSoup(get(t["web"]), "html.parser")
        sel = [l for l in enlaces(home, t["web"], t["org"], es_categoria)
               if re.search(r"caf|coffee|grano|tienda|shop|origen|especial", l, re.I) and not NEG_URL.search(up.urlparse(l).path)]
        tiendas = []
        for a in home.find_all("a", href=True):
            u = up.urljoin(t["web"], a["href"]).split("#")[0].split("?")[0]; pu = up.urlparse(u)
            if (dominio(pu.netloc) != dominio(up.urlparse(t["org"]).netloc) or u in tiendas or u.rstrip("/") == t["web"].rstrip("/")
                    or EXCL.search(pu.path) or NEG_URL.search(pu.path) or es_producto(pu.path)): continue
            if (re.search(r"tienda|shop|comprar|caf[eé]s?\b|productos|botiga|store|todos", a.get_text(" ", strip=True), re.I)
                    or re.search(r"/(tienda|shop|productos?|store|comprar|cafes?|collections)[\w-]*(/|$)", pu.path, re.I)): tiendas.append(u)
        tiendas += [h for h in {up.urljoin(t["web"], a["href"]) for a in home.find_all("a", href=True)} if re.match(r"https?://(tienda|shop|store)\.", h)]
        confiar, listados = bool(sel), sel or (tiendas + [t["web"]] + [t["org"] + r for r in RUTAS])
    vistos, fallos = [], []
    for base in listados[:8]:
        siguiente = None
        for pg in range(1, 21):
            if agotado(t): break
            cands = [base] if pg == 1 else ([siguiente] if siguiente else []) + [base + ("&" if "?" in base else "?") + f"page={pg}", base.rstrip("/") + f"/page/{pg}/"]
            nuevos = []
            for u in cands:
                try:
                    html = get(u); soup = BeautifulSoup(html, "html.parser")
                    nuevos = [l for l in (enlaces(soup, u, t["org"], es_producto) or enlaces_crudos(html, u, t["org"], es_producto) or enlaces_tarjeta(soup, u, t["org"])) if l not in vistos]
                    a = soup.find("a", rel="next") or soup.find("a", href=re.compile(r"[?&](?:offset|page)=\d"))
                    siguiente = up.urljoin(u, a["href"]) if a else None
                    if "squarespace" in html.lower():          # Squarespace ofrece el listado también en JSON
                        try:
                            j = get(u + ("&" if "?" in u else "?") + "format=json", True)
                            nuevos += [l for l in (up.urljoin(u, it["fullUrl"]) for it in j.get("items", []) if it.get("fullUrl"))
                                       if l not in vistos and l not in nuevos]
                            npu = (j.get("pagination") or {}).get("nextPageUrl")
                            if npu: siguiente = up.urljoin(u, npu)
                        except Exception: pass
                except Exception as e: nuevos = []; fallos.append(str(e)[:100])
                if nuevos: break
            if not nuevos: break
            vistos += nuevos; time.sleep(1)
    out = []
    for l in vistos[:150]:
        if agotado(t): break
        if NEG_URL.search(up.urlparse(l).path): continue
        try: f = pagina(t, l, confiar)
        except Exception as e: fallos.append(str(e)[:100]); continue
        if f: out.append(f)
        time.sleep(1)
    if not out and fallos: raise Exception("sin productos; " + " | ".join(fallos[:2]))
    return out

def sitemap(t):
    urls, cand, cola, hechos = [], [], [t["org"] + "/sitemap.xml"], set()
    while cola and len(hechos) < 8:
        sm = cola.pop(0)
        if sm in hechos: continue
        hechos.add(sm)
        try: xml = get(sm)
        except Exception as e:
            if len(hechos) == 1: raise
            continue
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
            loc = loc.replace("&amp;", "&")
            if loc.endswith(".xml") or "sitemap" in loc.split("/")[-1]:
                if re.search(r"product|producto|shop|tienda|store", loc, re.I) or len(hechos) == 1: cola.append(loc)
            elif es_producto(up.urlparse(loc).path): urls.append(loc)
            elif (not EXCL.search(up.urlparse(loc).path) and up.urlparse(loc).path.strip("/")
                  and not up.urlparse(loc).path.lower().endswith((".xml", ".pdf", ".jpg", ".jpeg", ".png", ".json", ".webp", ".gif"))): cand.append(loc)
    if not urls: urls = cand[:80]
    out = []
    for l in urls[:150]:
        if agotado(t): break
        if NEG_URL.search(up.urlparse(l).path): continue
        try: f = pagina(t, l)
        except Exception: continue
        if f: out.append(f)
        time.sleep(1)
    return out

def huella(t):
    try: h = get(t["web"])
    except Exception as e: return f"portada no accesible: {str(e)[:150]}"
    marcas = {"Shopify": "cdn.shopify.com", "WooCommerce": "woocommerce", "PrestaShop": "prestashop", "Wix": "wixstatic",
              "Squarespace": "squarespace", "Magento": "magento", "Webflow": "webflow"}
    pl = [k for k, v in marcas.items() if v in h.lower()] or ["desconocida"]
    soup = BeautifulSoup(h, "html.parser")
    ents = [f"{a.get_text(' ', strip=True)[:25]} -> {up.urljoin(t['web'], a['href'])}" for a in soup.find_all("a", href=True)
            if re.search(r"tienda|shop|comprar|store|productos|collections", a["href"] + " " + a.get_text(" "), re.I)]
    return f"plataforma: {pl}; enlaces de tienda: {ents[:8]}"

def estado(u, j=False):
    """Devuelve (resultado, texto): 'ok' o el motivo del fallo."""
    try:
        r = requests.get(u, headers=H, timeout=15)
        if r.status_code != 200: return f"HTTP {r.status_code}", ""
        if "just a moment" in r.text[:3000].lower(): return "bloqueado (Cloudflare)", ""
        if j:
            try: r.json()
            except Exception: return "no es JSON", ""
        return "ok", r.text
    except requests.Timeout: return "timeout", ""
    except Exception: return "sin conexión", ""

def diag_uno(t):
    p, home = estado(t["web"])
    marcas = {"Shopify": "cdn.shopify.com", "WooCommerce": "woocommerce", "PrestaShop": "prestashop", "Wix": "wixstatic",
              "Squarespace": "squarespace", "Magento": "magento", "Webflow": "webflow"}
    return {"tostador": t["nombre"], "web": t["web"], "portada": p,
            "plataforma": ", ".join(k for k, v in marcas.items() if v in home.lower()) or "?",
            "shopify": estado(t["org"] + "/products.json?limit=1", True)[0],
            "woocommerce": estado(t["org"] + "/wp-json/wc/store/v1/products?per_page=1", True)[0],
            "sitemap": estado(t["org"] + "/sitemap.xml")[0]}

def diagnostico():
    import sys
    filtro = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    ts = [t for t in leer_csv() if filtro in t["nombre"].lower()]
    with ThreadPoolExecutor(max_workers=8) as ex: filas = list(ex.map(diag_uno, ts))
    with open("diagnostico.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";"); w.writeheader(); w.writerows(filas)
    print(f"Guardado en: {os.path.abspath('diagnostico.csv')}\n")
    ok = [x for x in filas if x["shopify"] == "ok" or x["woocommerce"] == "ok"]
    print(f"{len(ok)} de {len(filas)} tostadores tienen una tienda Shopify/WooCommerce legible.\n\nA REVISAR (copia desde aquí):")
    for x in filas:
        if x not in ok:
            print(f"- {x['tostador']} | portada: {x['portada']} | {x['plataforma']} | shopify: {x['shopify']} | woo: {x['woocommerce']} | sitemap: {x['sitemap']}")

SOCIAL = re.compile(r"facebook|instagram|twitter|x\.com|youtube|tiktok|wa\.me|whatsapp|google|linkedin|pinterest|goo\.gl|linktr\.ee|amazon|tripadvisor|spotify|apple\.com", re.I)

def tiendas_externas(t):
    """Tiendas alojadas en otro dominio (Square, shopcafes.com...) enlazadas desde la portada."""
    try: home = BeautifulSoup(get(t["web"]), "html.parser")
    except Exception: return []
    res = []
    for a in home.find_all("a", href=True):
        pu = up.urlparse(up.urljoin(t["web"], a["href"]))
        if not pu.scheme.startswith("http") or SOCIAL.search(pu.netloc) or dominio(pu.netloc) == dominio(up.urlparse(t["org"]).netloc): continue
        if re.search(r"tienda|shop|botiga|comprar|store|compra|online", a.get_text(" ", strip=True) + " " + pu.netloc, re.I):
            org = f"{pu.scheme}://{pu.netloc}"
            if org not in res: res.append(org)
    return res[:2]

def intentar(t):
    metodos = [("shopify", shopify), ("woocommerce", woo), ("html", html_generico), ("sitemap", sitemap)]
    if t["cat"]: metodos = [metodos[2], metodos[0], metodos[1], metodos[3]]
    err = ""
    for nombre, f in metodos:
        t["limite"] = time.time() + 150      # plazo por método
        try:
            r = f(t); err += f"{nombre}: {len(r)}; "
            if r: return nombre, r, ""
        except Exception as e:
            err += f"{nombre}: error {str(e)[:150]}; "
            if "BLOQUEADO" in str(e): return "bloqueado", [], err      # si bloquea, no insistir con otros métodos
    return "ninguno", [], err

def procesar(t):
    LOC.rech = []
    m, r, e = intentar(t)
    r = filtrar_dudosos(r)
    if not r and m != "bloqueado":
        for org in tiendas_externas(t):
            m2, r2, e2 = intentar(dict(t, org=org, web=org, cat=""))
            if r2: m, r, e = f"{m2}@{up.urlparse(org).netloc}", r2, ""; break
            e += f"[{org}] {e2}"
    if t.get("excl"): r = [c for c in r if not any(w in c["n"].lower() for w in t["excl"])]
    t["_rech"] = list(LOC.rech)
    return m, r, e

def exportar_csv():
    datos = json.load(open("cafes.json", encoding="utf-8"))
    with open("cafes_lista.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["tostador", "ciudad", "cafe", "precio", "gramos", "precio_250g", "origen", "proceso", "notas", "sca", "url"])
        for c in sorted(datos, key=lambda c: (c.get("r", ""), c.get("n", ""))):
            w.writerow([c.get("r"), c.get("c"), c.get("n"), c.get("pr"), c.get("g"), c.get("p250"), c.get("o"), c.get("p"),
                        ", ".join(c.get("nt") or []), c.get("sca"), c.get("u")])
    print(f"{len(datos)} cafés en {os.path.abspath('cafes_lista.csv')}")

GURL = "https://places.googleapis.com/v1/places:searchText"
STOP = {"coffee", "cafe", "cafes", "roaster", "roasters", "roasting", "specialty", "speciality", "de", "la", "el", "los", "las", "del", "y", "and", "tostador", "tostadores", "especialidad", "co", "lab"}
def toks(x): return {w for w in re.findall(r"[a-z0-9]+", sa(x)) if w not in STOP and len(w) > 1}

def google_ratings(tostadores):
    """Nota de Google Maps por tostador. Requiere la variable GOOGLE_API_KEY; se refresca cada 7 días (google.json)."""
    key = os.environ.get("GOOGLE_API_KEY")
    cache = json.load(open("google.json", encoding="utf-8")) if os.path.exists("google.json") else {}
    if not key:
        if not cache: print("[Google] Sin GOOGLE_API_KEY: no se añaden valoraciones de Google.")
        return cache
    hoy = date.today()
    for t in tostadores:
        c = cache.get(t["nombre"])
        if c and (hoy - date.fromisoformat(c["fecha"])).days < 7: continue
        try:
            r = requests.post(GURL, json={"textQuery": f"{t['nombre']} {t['ciudad']}", "languageCode": "es", "regionCode": "ES"},
                headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id,places.displayName,places.rating,places.userRatingCount,places.formattedAddress"}, timeout=20)
            r.raise_for_status()
            cache[t["nombre"]] = {"g": None, "gn": None, "encontrado": "", "fecha": hoy.isoformat()}
            for p in r.json().get("places", []):
                ng = (p.get("displayName") or {}).get("text", "")
                if p.get("rating") and toks(t["nombre"]) & toks(ng) and (not t["ciudad"] or sa(t["ciudad"]) in sa(p.get("formattedAddress", ""))):
                    cache[t["nombre"]].update(g=p["rating"], gn=p.get("userRatingCount"), encontrado=ng); break
        except Exception as e: print("[Google]", t["nombre"], str(e)[:100])
        time.sleep(0.2)
    json.dump(cache, open("google.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return cache

def cargar_manuales(tostadores):
    """Cafés que añades a mano (tiendas bloqueadas, sin web...): cafes_manuales.csv"""
    if not os.path.exists("cafes_manuales.csv"): return []
    raw = open("cafes_manuales.csv", "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try: txt = raw.decode(enc); break
        except UnicodeDecodeError: pass
    lin = txt.splitlines(); d = ";" if lin[0].count(";") >= lin[0].count(",") else ","
    por = {t["nombre"].lower(): t for t in tostadores}; out = []
    for r in csv.DictReader(lin, delimiter=d):
        n, nom, precio = (r.get("Cafe") or "").strip(), (r.get("Tostador") or "").strip(), num(r.get("Precio"))
        if not (n and nom and precio): continue
        t = por.get(nom.lower()) or {"nombre": nom, "ciudad": (r.get("Ciudad") or "").strip(), "sh": "", "mn": "", "param": "ref", "code": "", "aff": "", "tpl": "", "web": ""}
        g = int(num(r.get("Gramos")) or 0) or None
        f = ficha(t, n, (r.get("URL") or "").strip() or t.get("web", ""), (r.get("Imagen") or "").strip(),
                  f"{n} {r.get('Origen','')} {r.get('Proceso','')} {r.get('Notas','')}", precio,
                  (r.get("Disponible") or "1").strip().lower() not in ("0", "no"), g, "manual")
        for k, col in (("o", "Origen"), ("p", "Proceso")):
            if (r.get(col) or "").strip(): f[k] = r[col].strip()
        if num(r.get("SCA")): f["sca"] = num(r.get("SCA"))
        f.pop("_d", None); f["manual"] = True
        a = parse_fecha(r.get("Actualizado"))
        if a: f["act"] = a.isoformat()
        out.append(f)
    return out

def leer_tabla(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try: txt = raw.decode(enc); break
        except UnicodeDecodeError: pass
    lin = txt.splitlines()
    return list(csv.DictReader(lin, delimiter=";" if lin[0].count(";") >= lin[0].count(",") else ",")) if lin else []

def parse_fecha(x):
    x = (x or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(x, fmt).date()
        except ValueError: pass
    return None

def aplicar_correcciones(total):
    """correcciones.csv: arreglos manuales sobre cafés leídos automáticamente. Se reaplican en cada ejecución."""
    if not os.path.exists("correcciones.csv"): return total
    k = lambda u: (u or "").split("#")[0].rstrip("/").lower()
    por_url = {k(c["u"]): c for c in total}
    por_nom = {(c["r"].lower(), c["n"].lower()): c for c in total}
    ocultos, n_ok, hoy = set(), 0, date.today()
    for r in leer_tabla("correcciones.csv"):
        hasta = parse_fecha(r.get("Hasta"))
        if hasta and hasta < hoy: continue                 # corrección caducada: vuelve el dato automático
        c = por_url.get(k(r.get("URL"))) or por_nom.get(((r.get("Tostador") or "").strip().lower(), (r.get("Nombre") or "").strip().lower()))
        if not c: print("[correcciones] no encontrado:", r.get("URL") or r.get("Nombre")); continue
        if (r.get("Ocultar") or "").strip().lower() in ("1", "si", "sí", "x"): ocultos.add(id(c)); continue
        v = lambda col: (r.get(col) or "").strip()
        if v("Nombre_Nuevo"): c["n"] = v("Nombre_Nuevo")
        if v("Origen"): c["o"] = v("Origen")
        if v("Proceso"): c["p"] = v("Proceso")
        if v("Notas"): c["nt"] = [x for x in (hallar(NOTAS, v("Notas")) or [y.strip() for y in re.split(r"[,;|]", v("Notas")) if y.strip()])]
        if num(v("SCA")): c["sca"] = num(v("SCA"))
        if num(v("Precio")): c["pr"] = round(num(v("Precio")), 2)
        if num(v("Gramos")): c["g"], c["gsrc"] = int(num(v("Gramos"))), "manual"
        if v("Disponible"): c["s"] = v("Disponible").lower() not in ("0", "no")
        c["p250"] = round(c["pr"] * 250 / c["g"], 2) if c.get("g") else None
        c["corr"] = True; n_ok += 1
    print(f"[correcciones] {n_ok} aplicadas, {len(ocultos)} cafés ocultos")
    return [c for c in total if id(c) not in ocultos]

def plantilla_correcciones():
    """Crea correcciones_plantilla.csv con los cafés con avisos de precio (no toca correcciones.csv)."""
    datos = json.load(open("cafes.json", encoding="utf-8"))
    with open("correcciones_plantilla.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["URL", "Tostador", "Nombre", "Aviso", "Precio_actual", "Gramos_actual", "Nombre_Nuevo", "Origen", "Proceso", "Notas", "SCA", "Precio", "Gramos", "Disponible", "Ocultar", "Hasta"])
        n = 0
        for c in datos:
            p = c.get("p250"); av = []
            if not c.get("g"): av.append("gramos desconocidos")
            if p and (p < 4 or p > 70): av.append("precio por 250 g inusual")
            if not c.get("o"): av.append("sin origen")
            if av: n += 1; w.writerow([c.get("u"), c.get("r"), c.get("n"), "; ".join(av), c.get("pr"), c.get("g"), "", "", "", "", "", "", "", "", "", ""])
    print(f"{n} cafés con avisos -> {os.path.abspath('correcciones_plantilla.csv')} (cópialo como correcciones.csv y rellena solo lo que quieras cambiar)")

def auditoria_precios():
    datos = json.load(open("cafes.json", encoding="utf-8")); avisos = 0
    with open("auditoria_precios.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["tostador", "cafe", "precio", "gramos", "fuente_gramos", "precio_250g", "aviso", "url"])
        for c in sorted(datos, key=lambda c: (c.get("r", ""), c.get("n", ""))):
            av, p = [], c.get("p250")
            if not c.get("g"): av.append("gramos desconocidos: no se puede comparar por 250 g")
            elif c.get("gsrc") == "peso_envio": av.append("gramos estimados por el peso de envío")
            if p and p < 4: av.append("precio por 250 g sospechosamente bajo")
            if p and p > 70: av.append("precio por 250 g muy alto (¿formato pequeño o error?)")
            if (c.get("pr") or 0) > 100: av.append("precio superior a 100 €")
            avisos += bool(av)
            w.writerow([c.get("r"), c.get("n"), c.get("pr"), c.get("g"), c.get("gsrc"), p, "; ".join(av), c.get("u")])
    print(f"{len(datos)} cafés, {avisos} con algún aviso -> {os.path.abspath('auditoria_precios.csv')}")

def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--precios": return auditoria_precios()
    if len(sys.argv) > 1 and sys.argv[1] == "--plantilla": return plantilla_correcciones()
    if len(sys.argv) > 1 and sys.argv[1] == "--csv": return exportar_csv()
    global VERBOSO
    if len(sys.argv) > 1 and sys.argv[1] == "--diagnostico": return diagnostico()
    filtro = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    tostadores = [t for t in leer_csv() if filtro in t["nombre"].lower()]
    print(f"[*] {len(tostadores)} tostadores")
    VERBOSO = bool(filtro)
    if filtro:   # modo diagnóstico: python scraper.py "Despiertoo"
        for t in tostadores:
            print(huella(t))
            metodo, cafes, err = procesar(t)
            print(f"\n== {t['nombre']}: método {metodo}, {len(cafes)} cafés. {err}")
            for c in cafes[:10]: print("  ", c["n"], c["pr"], c["o"], c["mt"], c["nt"])
            print("   rechazados:", t.get("_rech", [])[:25])
        return
    viejo = json.load(open("cafes.json", encoding="utf-8")) if os.path.exists("cafes.json") else []
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(procesar, tostadores))
    total, informe = [], []
    for t, (metodo, cafes, err) in zip(tostadores, res):
        pista = ""
        if not cafes:
            pista = huella(t)
            cafes = [c for c in viejo if c.get("r") == t["nombre"] and (c.get("pr") or 0) > 0]; err = (err + " (se conservan datos anteriores)").strip()
        total += cafes
        informe.append({"tostador": t["nombre"], "metodo": metodo, "cafes": len(cafes), "error": err, "pista": pista, "rechazados": " | ".join(t.get("_rech", [])[:8])})
        print(f"{metodo:12} {len(cafes):3}  {t['nombre']} {err}")
    total += cargar_manuales(tostadores)
    vistos_u, unicos = set(), []
    for c in total:                                    # sin duplicados (mismo enlace)
        k = (c.get("u") or "").split("#")[0].rstrip("/")
        if k and k in vistos_u: continue
        vistos_u.add(k); unicos.append(c)
    total = aplicar_correcciones(unicos)
    gg = google_ratings(tostadores) if os.environ.get("USAR_GOOGLE") == "1" else {}   # desactivado por defecto (condiciones de Google)
    for c in total:
        x = gg.get(c["r"])
        if x and x.get("g"): c["g"], c["gn"] = x["g"], x.get("gn")
    for i, c in enumerate(total): c["i"] = i
    json.dump(total, open("cafes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"fecha": datetime.now().isoformat(timespec="seconds"), "total_cafes": len(total),
               "total_tostadores": len(tostadores), "sin_datos": [x["tostador"] for x in informe if x["cafes"] == 0]},
              open("estado.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open("informe.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tostador", "metodo", "cafes", "error", "pista", "rechazados"], delimiter=";")
        w.writeheader(); w.writerows(informe)
    print(f"[ok] {len(total)} cafés de {len(tostadores)} tostadores")

if __name__ == "__main__":
    main()
