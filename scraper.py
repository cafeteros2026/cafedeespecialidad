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

H = {"User-Agent": "Mozilla/5.0 (compatible; GranosBot/1.0; +https://github.com/)"}
ORIG = ["Etiopía","Kenia","Colombia","Brasil","Guatemala","Costa Rica","Perú","Honduras","El Salvador","Panamá",
        "Ruanda","Burundi","Uganda","Venezuela","Nicaragua","México","Indonesia","Tanzania","Bolivia","Ecuador","Yemen","India"]
PROC = {"Natural":["natural"],"Lavado":["lavado","washed"],"Honey":["honey"],"Anaeróbico":["anaer"]}
NOTAS = {"Frutos rojos":["frutos rojos","fresa","frambuesa","arándano","grosella","cereza"],
 "Cítrico":["cítric","naranja","limón","lima","mandarina","bergamota","pomelo"],"Floral":["floral","jazmín","flores"],
 "Chocolate":["chocolate","cacao"],"Caramelo":["caramelo","panela","toffee"],"Miel":["miel"],
 "Frutos secos":["frutos secos","almendra","avellana","nuez"],"Té":["té negro","black tea"]}
NO = ["taza","merch","camiseta","molinillo","cafetera","papel","v60","aeropress","gift","regalo","tote","curso",
      "suscrip","pack","prensa","tetera","báscula","bascula","jarra","cápsula","capsula","sudadera"]

def get(u, j=False):
    r = requests.get(u, headers=H, timeout=15); r.raise_for_status()
    return r.json() if j else r.text

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
def gramos(s):
    m = re.search(r"(\d{3,4})\s?(?:g|gr)\b", s or "", re.I); return int(m.group(1)) if m else None
def hallar(d, t): return [k for k, v in d.items() if any(re.search(r"\b" + re.escape(x), t, re.I) for x in v)]
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
        "t": "Espresso" if "espresso" in tl and "filtro" not in tl else "Filtro" if "filtro" in tl and "espresso" not in tl else "Ambos",
        "o": pais(n, txt), "p": (hallar(PROC, txt) or [""])[0], "nt": hallar(NOTAS, txt), "pr": pr, "s": bool(stock),
        "sca": sca(txt), "rt": None, "sh": num(t["sh"]), "mn": num(t["mn"]) or 0}

def norm(s): return re.sub(r"\s+", " ", s or "").strip()
def texto(h): return norm(BeautifulSoup(h or "", "html.parser").get_text(" "))

def shopify(t):
    out = []
    for pg in range(1, 11):
        d = get(f"{t['org']}/products.json?limit=250&page={pg}", True)["products"]
        if not d: break
        for p in d:
            tags = p.get("tags") or []; tags = tags.split(",") if isinstance(tags, str) else tags
            cab = f"{p['title']} {p.get('product_type','')} {' '.join(tags)}"
            vs = p.get("variants") or []
            pesos = any(gramos(v.get("title", "")) for v in vs)
            if no_cafe(cab) or not (pesos or re.search(r"caf[eé]|coffee|espresso|filtro|grano|blend", cab, re.I)): continue
            mejor = None
            for v in vs:
                g = gramos(v.get("title", "")); pr = float(v["price"])
                if mejor is None or g == 250: mejor = (pr, g)
            if not mejor: continue
            out.append(ficha(t, p["title"], f"{t['org']}/products/{p['handle']}", p["images"][0]["src"] if p.get("images") else "",
                f"{cab} {texto(p.get('body_html'))}", mejor[0], any(v.get("available") for v in vs), mejor[1]))
        time.sleep(1)
    return out

def woo(t):
    out = []
    for pg in range(1, 11):
        d = get(f"{t['org']}/wp-json/wc/store/v1/products?per_page=100&page={pg}", True)
        if not d: break
        for p in d:
            cats = " ".join(c["name"] for c in p.get("categories", []))
            if no_cafe(f"{p['name']} {cats}"): continue
            pr = p["prices"]
            out.append(ficha(t, p["name"], p["permalink"], p["images"][0]["src"] if p.get("images") else "",
                f"{p['name']} {cats} {texto(p.get('short_description'))} {texto(p.get('description'))}",
                int(pr["price"]) / 10 ** pr.get("currency_minor_unit", 2), p.get("is_in_stock", True), gramos(p["name"])))
        if len(d) < 100: break
        time.sleep(1)
    return out

def es_producto(path):   # /es/cafetazos/207-1700-black-lime  (PrestaShop)
    s = path.strip("/").split("/")
    return bool(re.fullmatch(r"\d+-[\w-]+(\.html)?", s[-1])) and any(not re.fullmatch(r"[a-z]{2}", x) for x in s[:-1])

def es_categoria(path):  # /es/3-cafetazos
    s = path.strip("/").split("/")
    return bool(re.fullmatch(r"\d+-[\w-]+", s[-1])) and all(re.fullmatch(r"[a-z]{2}", x) for x in s[:-1])

def enlaces(soup, base, org, fn):
    res = []
    for a in soup.find_all("a", href=True):
        u = up.urljoin(base, a["href"]).split("#")[0].split("?")[0]
        pu = up.urlparse(u)
        if pu.netloc == up.urlparse(org).netloc and fn(pu.path) and u not in res: res.append(u)
    return res

def pagina(t, u):
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
    if not n or pr is None or no_cafe(n + u): return None
    img = (prod or {}).get("image") or meta("og:image")
    img = img[0] if isinstance(img, list) and img else img.get("url", "") if isinstance(img, dict) else img
    desc = " ".join(e.get_text(" ") for e in soup.select("[itemprop=description], .product-description, #description, .product-information"))
    txt = f"{n} {(prod or {}).get('description','')} {desc} {meta('og:description')}"
    stock = "OutOfStock" not in str(of.get("availability", ""))
    return ficha(t, n, u, img if isinstance(img, str) else "", txt, pr, stock, gramos(n))

def html_generico(t):
    if t["cat"]: listados = [t["cat"]]
    else:
        home = BeautifulSoup(get(t["web"]), "html.parser")
        listados = [l for l in enlaces(home, t["web"], t["org"], es_categoria)
                    if re.search(r"caf|coffee|grano|tienda|shop|origen|especial", l, re.I)] or [t["web"]]
    vistos = []
    for base in listados[:5]:
        for pg in range(1, 21):
            u = base if pg == 1 else base + ("&" if "?" in base else "?") + f"page={pg}"
            try: nuevos = [l for l in enlaces(BeautifulSoup(get(u), "html.parser"), u, t["org"], es_producto) if l not in vistos]
            except Exception: break
            if not nuevos: break
            vistos += nuevos; time.sleep(1)
    out = []
    for l in vistos[:300]:
        try: f = pagina(t, l)
        except Exception: continue
        if f: out.append(f)
        time.sleep(1)
    return out

def procesar(t):
    metodos = [("shopify", shopify), ("woocommerce", woo), ("html", html_generico)]
    if t["cat"]: metodos = metodos[::-1]
    err = ""
    for nombre, f in metodos:
        try:
            r = f(t)
            if r: return nombre, r, ""
        except Exception as e: err += f"{nombre}: {str(e)[:80]}; "
    return "ninguno", [], err or "0 productos"

def main():
    tostadores = leer_csv()
    print(f"[*] {len(tostadores)} tostadores en el CSV")
    viejo = json.load(open("cafes.json", encoding="utf-8")) if os.path.exists("cafes.json") else []
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(procesar, tostadores))
    total, informe = [], []
    for t, (metodo, cafes, err) in zip(tostadores, res):
        if not cafes:  # si falla, conserva lo anterior para no vaciar la web
            cafes = [c for c in viejo if c.get("r") == t["nombre"]]; err = (err + " (se conservan datos anteriores)").strip()
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
