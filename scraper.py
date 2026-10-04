"""
scraper.py: lee los cafés de cada tostador de lista_tostadores.csv y genera cafes.json,
estado.json e informe.csv (qué ha pasado con cada tostador).
Orden de métodos por tostador: Shopify -> WooCommerce -> HTML genérico (PrestaShop y otros).
Columnas opcionales en el CSV: URL_Catalogo (página de listado de cafés, recomendada para
tiendas que no son Shopify/Woo), Envio_Gratis, Pedido_Minimo.
"""
import csv, json, os, re, time, urllib.parse as up
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import requests
from bs4 import BeautifulSoup

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "Accept-Language": "es-ES,es;q=0.9,en;q=0.8", "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
RECHAZ = []
VERBOSO = False
def log(*a):
    if VERBOSO: print(*a, flush=True)
def agotado(t): return time.time() > t.get("limite", 1e18)   # plazo máximo por tostador
ORIG = ["Etiopía","Kenia","Colombia","Brasil","Guatemala","Costa Rica","Perú","Honduras","El Salvador","Panamá",
        "Ruanda","Burundi","Uganda","Venezuela","Nicaragua","México","Indonesia","Tanzania","Bolivia","Ecuador","Yemen","India"]
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

def get(u, j=False):
    log('  GET', u)
    for i in range(3):
        r = requests.get(u, headers=H, timeout=20)
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
                "aff": (r.get("Affiliate_URL") or "").strip()})
    return out

def num(v):
    try: return float(str(v).replace(",", "."))
    except Exception: return None

def no_cafe(s): return any(x in s.lower() for x in NO)
NEG = re.compile(r"\b(?:molinill|grinder|m[aá]quina|cafeter|accesorio|merch|camiseta|gorra|sudadera|tote|regalo|gift|tarjeta|curso|formaci|taller|suscrip|pack|kit\b|b[aá]scula|jarra|tetera|hervidor|limpi|descalcific|tamper|portafiltro|dripper|chemex|aeropress|c[aá]psula|taza(?! de excelencia)|vaso|termo|prueba|test\b|comprobaci|tools|equipamiento|herramienta|libro|delantal|pegatina|p[oó]ster|mug\b|filtros? de papel|papel|nuez|nueces|pipas|pasas|cacahuete|almendra|avellana|pistacho|anacardo|d[aá]til|higo|orejones|frutos secos|snack|galleta|turr[oó]n|aceite|cerveza|infusi|tisana|rooibos|matcha|chai|t[eé] (?:verde|negro|rojo|blanco)|vino|licor|tableta|bomb[oó]n|bizcocho)", re.I)
POS = re.compile(r"caf[eé]\b|coffee|grano|espresso|origen|tueste|blend|especialidad|single|microlote|descafe|decaf", re.I)

CAFE_TXT = re.compile(r"tueste|ar[aá]bica|espresso|molido|\bgranos?\b|cafetera|altitud|\bcata\b|finca|caf[eé] de especialidad", re.I)

def es_cafe(nombre, cab, desc, tiene_peso, precio, confiado=False):
    """Solo café: descarta accesorios, máquinas, merch y otros alimentos; exige evidencia de café."""
    def no(m): RECHAZ.append(f"{nombre} [{m}]"); return False
    if precio is None or precio <= 0: return no("sin precio")
    if NEG.search(f"{nombre} {cab}"): return no("accesorio/alimento/merch")
    if precio > 60 and not tiene_peso: return no("precio alto sin gramos")
    if confiado or POS.search(f"{nombre} {cab}"): return True
    if tiene_peso and pais(nombre, ""): return True       # p. ej. "Colombia Huila 250 g"
    ev = CAFE_TXT.search(desc) or (pais(nombre, desc) and re.search(r"notas|tueste|cata", desc, re.I))
    return True if ev and (tiene_peso or precio < 40) else no("sin evidencia de café")

def gramos(s):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?(kg|kilos?|gramos|gr|g)\b", s or "", re.I)
    if not m: return None
    v = float(m.group(1).replace(",", ".")) * (1000 if m.group(2).lower().startswith("k") else 1)
    return int(v) if 100 <= v <= 5000 else None
def hallar(d, t): return [k for k, v in d.items() if any(re.search(r"\b" + re.escape(x) + r"(?:s|es|os|as|a|o)?\b", t, re.I) for x in v)]
def sca(t):
    m = re.search(r"(?:sca|puntuaci[oó]n|score)\D{0,12}(8\d(?:[.,]\d)?|9[0-5](?:[.,]\d)?)|(8\d(?:[.,]\d)?|9[0-5](?:[.,]\d)?)\s*(?:pts|puntos|points|sca)\b", t, re.I)
    return float((m.group(1) or m.group(2)).replace(",", ".")) if m else None
def pais(titulo, txt):
    for src in (titulo, txt):
        for p in ORIG:
            if re.search(r"\b" + p, src, re.I): return p
    return "Blend" if "blend" in titulo.lower() else ""

def afiliado(u, t):
    if t["aff"]: return t["aff"]
    q = up.parse_qs(up.urlparse(u).query)
    if t["code"]: q[t["param"]] = [t["code"]]
    q["utm_source"] = ["granos"]
    return up.urlunparse(up.urlparse(u)._replace(query=up.urlencode(q, doseq=True)))

def ficha(t, n, u, img, txt, precio, stock, g=None):
    pr = round(precio * 250 / g, 2) if g and g != 250 else round(precio, 2)
    tl = txt.lower()
    return {"n": norm(n), "r": t["nombre"], "c": t["ciudad"], "u": u, "aff_url": afiliado(u, t), "img": img or "",
        "mt": hallar(METODOS, txt),
        "o": pais(n, txt), "p": (hallar(PROC, txt) or [""])[0], "nt": hallar(NOTAS, txt), "pr": pr, "s": bool(stock),
        "sca": sca(txt), "rt": None, "sh": num(t["sh"]), "mn": num(t["mn"]) or 0}

def norm(s): return re.sub(r"\s+", " ", s or "").strip()
def texto(h): return norm(BeautifulSoup(h or "", "html.parser").get_text(" "))

def shopify(t):
    out = []
    for pg in range(1, 11):
        if agotado(t): break
        d = get(f"{t['org']}/products.json?limit=250&page={pg}", True)["products"]
        if not d: break
        for p in d:
            tags = p.get("tags") or []; tags = tags.split(",") if isinstance(tags, str) else tags
            cab = f"{p['title']} {p.get('product_type','')} {' '.join(tags)}"
            vs = p.get("variants") or []
            pesos = any(gramos(v.get("title", "")) for v in vs)
            mejor = None
            for v in vs:
                g = gramos(v.get("title", "")); pr = float(v["price"])
                if mejor is None or g == 250: mejor = (pr, g)
            if not mejor or not es_cafe(p["title"], f"{p.get('product_type','')} {' '.join(tags)}", texto(p.get('body_html')), pesos, mejor[0]): continue
            out.append(ficha(t, p["title"], f"{t['org']}/products/{p['handle']}", p["images"][0]["src"] if p.get("images") else "",
                f"{cab} {texto(p.get('body_html'))} {' '.join(v.get('title','') for v in vs)}", mejor[0], any(v.get("available") for v in vs), mejor[1]))
        time.sleep(1)
    return out

def woo(t):
    out = []
    for pg in range(1, 11):
        if agotado(t): break
        d = get(f"{t['org']}/wp-json/wc/store/v1/products?per_page=100&page={pg}", True)
        if not d: break
        for p in d:
            cats = " ".join(c["name"] for c in p.get("categories", []))
            pr = p["prices"]; precio = int(pr.get("price") or 0) / 10 ** pr.get("currency_minor_unit", 2)
            attr = " ".join(x["name"] for a in p.get("attributes", []) for x in a.get("terms", []))
            desc = f"{texto(p.get('short_description'))} {texto(p.get('description'))}"
            if not es_cafe(p["name"], cats, desc, bool(gramos(p["name"]) or gramos(desc) or gramos(attr)), precio): continue
            out.append(ficha(t, p["name"], p["permalink"], p["images"][0]["src"] if p.get("images") else "",
                f"{p['name']} {cats} {attr} {desc}",
                precio, p.get("is_in_stock", True), gramos(p["name"])))
        if len(d) < 100: break
        time.sleep(1)
    return out

RUTAS = ["/tienda", "/shop", "/productos", "/cafe", "/cafes", "/collections/all", "/tienda-online"]

def es_producto(path):   # PrestaShop /es/cafetazos/207-1700-x  |  Woo /producto/x  |  Shopify /products/x
    s = path.strip("/").split("/")
    if re.fullmatch(r"\d+-[\w-]+(\.html)?", s[-1]) and any(not re.fullmatch(r"[a-z]{2}", x) for x in s[:-1]): return True
    return len(s) >= 2 and s[-2] in ("producto", "productos", "product", "products", "p", "tienda", "shop")

def es_categoria(path):  # /es/3-cafetazos
    s = path.strip("/").split("/")
    return bool(re.fullmatch(r"\d+-[\w-]+", s[-1])) and all(re.fullmatch(r"[a-z]{2}", x) for x in s[:-1])

def dominio(h): return ".".join(h.split(".")[-2:])

def enlaces(soup, base, org, fn):
    res = []
    for a in soup.find_all("a", href=True):
        u = up.urljoin(base, a["href"]).split("#")[0].split("?")[0]
        pu = up.urlparse(u)
        if dominio(pu.netloc) == dominio(up.urlparse(org).netloc) and fn(pu.path) and u not in res: res.append(u)
    return res

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
    pr = num(of.get("price") or meta("product:price:amount"))
    if not n or pr is None: return None
    img = (prod or {}).get("image") or meta("og:image")
    img = img[0] if isinstance(img, list) and img else img.get("url", "") if isinstance(img, dict) else img
    desc = " ".join(e.get_text(" ") for e in soup.select("[itemprop=description], .product-description, #description, .product-information"))
    txt = f"{n} {(prod or {}).get('description','')} {desc} {meta('og:description')}"
    if not es_cafe(n, up.urlparse(u).path, f"{desc} {meta('og:description')}", bool(gramos(n) or gramos(desc)), pr, confiar): return None
    stock = "OutOfStock" not in str(of.get("availability", ""))
    return ficha(t, n, u, img if isinstance(img, str) else "", txt, pr, stock, gramos(n))

def html_generico(t):
    confiar = bool(t["cat"])
    if t["cat"]: listados = [t["cat"]]
    else:
        home = BeautifulSoup(get(t["web"]), "html.parser")
        sel = [l for l in enlaces(home, t["web"], t["org"], es_categoria)
               if re.search(r"caf|coffee|grano|tienda|shop|origen|especial", l, re.I) and not NEG.search(up.urlparse(l).path)]
        tiendas = [l for l in enlaces(home, t["web"], t["org"], lambda p: bool(re.search(r"/(tienda|shop|productos?|store|comprar|cafes?|collections)(/|$)", p, re.I))) if not NEG.search(up.urlparse(l).path)]
        tiendas += [h for h in {up.urljoin(t["web"], a["href"]) for a in home.find_all("a", href=True)} if re.match(r"https?://(tienda|shop|store)\.", h)]
        confiar, listados = bool(sel), sel or (tiendas + [t["web"]] + [t["org"] + r for r in RUTAS])
    vistos = []
    for base in listados[:8]:
        for pg in range(1, 21):
            if agotado(t): break
            cands = [base] if pg == 1 else [base + ("&" if "?" in base else "?") + f"page={pg}", base.rstrip("/") + f"/page/{pg}/"]
            nuevos = []
            for u in cands:
                try: nuevos = [l for l in enlaces(BeautifulSoup(get(u), "html.parser"), u, t["org"], es_producto) if l not in vistos]
                except Exception: nuevos = []
                if nuevos: break
            if not nuevos: break
            vistos += nuevos; time.sleep(1)
    out = []
    for l in vistos[:150]:
        if agotado(t): break
        if NEG.search(up.urlparse(l).path): continue
        try: f = pagina(t, l, confiar)
        except Exception: continue
        if f: out.append(f)
        time.sleep(1)
    return out

def sitemap(t):
    urls, cola, hechos = [], [t["org"] + "/sitemap.xml"], set()
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
                if re.search(r"product|producto|shop|tienda|store", loc, re.I): cola.append(loc)
            elif es_producto(up.urlparse(loc).path): urls.append(loc)
    out = []
    for l in urls[:150]:
        if agotado(t): break
        if NEG.search(up.urlparse(l).path): continue
        try: f = pagina(t, l)
        except Exception: continue
        if f: out.append(f)
        time.sleep(1)
    return out

def huella(t):
    try: h = get(t["web"])
    except Exception as e: return f"portada no accesible: {str(e)[:150]}"
    marcas = {"Shopify": "cdn.shopify.com", "WooCommerce": "woocommerce", "PrestaShop": "prestashop", "Wix": "wixstatic",
              "Squarespace": "squarespace", "Magento": "mage-", "Webflow": "webflow"}
    pl = [k for k, v in marcas.items() if v in h.lower()] or ["desconocida"]
    soup = BeautifulSoup(h, "html.parser")
    ents = [f"{a.get_text(' ', strip=True)[:25]} -> {up.urljoin(t['web'], a['href'])}" for a in soup.find_all("a", href=True)
            if re.search(r"tienda|shop|comprar|store|productos|collections", a["href"] + " " + a.get_text(" "), re.I)]
    return f"plataforma: {pl}; enlaces de tienda: {ents[:8]}"

def procesar(t):
    metodos = [("shopify", shopify), ("woocommerce", woo), ("html", html_generico), ("sitemap", sitemap)]
    if t["cat"]: metodos = [metodos[2], metodos[0], metodos[1], metodos[3]]
    err = ""; t["limite"] = time.time() + 240
    for nombre, f in metodos:
        try:
            r = f(t); err += f"{nombre}: {len(r)}; "
            if r: return nombre, r, ""
        except Exception as e: err += f"{nombre}: error {str(e)[:150]}; "
    return "ninguno", [], err

def main():
    import sys
    global VERBOSO
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
            print("   rechazados:", RECHAZ[:25])
        return
    viejo = json.load(open("cafes.json", encoding="utf-8")) if os.path.exists("cafes.json") else []
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(procesar, tostadores))
    total, informe = [], []
    for t, (metodo, cafes, err) in zip(tostadores, res):
        if not cafes:
            cafes = [c for c in viejo if c.get("r") == t["nombre"] and (c.get("pr") or 0) > 0]; err = (err + " (se conservan datos anteriores)").strip()
        total += cafes
        informe.append({"tostador": t["nombre"], "metodo": metodo, "cafes": len(cafes), "error": err})
        print(f"{metodo:12} {len(cafes):3}  {t['nombre']} {err}")
    for i, c in enumerate(total): c["i"] = i
    json.dump(total, open("cafes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"fecha": datetime.now().isoformat(timespec="seconds"), "total_cafes": len(total),
               "total_tostadores": len(tostadores), "sin_datos": [x["tostador"] for x in informe if x["cafes"] == 0]},
              open("estado.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open("informe.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tostador", "metodo", "cafes", "error"], delimiter=";")
        w.writeheader(); w.writerows(informe)
    print(f"[ok] {len(total)} cafés de {len(tostadores)} tostadores")

if __name__ == "__main__":
    main()
