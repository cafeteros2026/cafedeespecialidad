import csv
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

PAISES_ORIGEN = [
    "Etiopía", "Kenia", "Colombia", "Brasil", "Guatemala", 
    "Costa Rica", "Perú", "Honduras", "El Salvador", "Panamá", "Ruanda", "Burundi"
]

PROCESOS = ["Lavado", "Natural", "Honey", "Anaeróbico", "Maceración", "Koji"]

NOTAS_CATA = [
    "Frutos rojos", "Cítrico", "Floral", "Chocolate", 
    "Caramelo", "Miel", "Frutos secos", "Té"
]

def cargar_tostadores_csv(filepath="lista_tostadores.csv"):
    tostadores = []
    try:
        with open(filepath, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                nombre = row.get("Tostador", "").strip()
                url = row.get("URL", "").strip()
                if nombre and url:
                    tostadores.append({
                        "nombre": nombre,
                        "url_base": url.rstrip("/"),
                        "ciudad": row.get("Ciudad", "").strip(),
                        "aff_param": row.get("Affiliate_Param", "").strip() or "ref",
                        "aff_code": row.get("Affiliate_Code", "").strip() or "granos",
                        "aff_url": row.get("Affiliate_URL", "").strip()
                    })
    except Exception as e:
        print(f"[!] Error al leer CSV {filepath}: {e}")
    return tostadores

def construir_url_afiliado(url_producto, tostador):
    if tostador.get("aff_url"):
        return tostador["aff_url"]
    try:
        parsed = urllib.parse.urlparse(url_producto)
        query = urllib.parse.parse_qs(parsed.query)
        if tostador.get("aff_param") and tostador.get("aff_code"):
            query[tostador["aff_param"]] = [tostador["aff_code"]]
        query["utm_source"] = ["granos"]
        query["utm_medium"] = ["referral"]
        new_query = urllib.parse.urlencode(query, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url_producto

def extraer_origen(texto):
    for pais in PAISES_ORIGEN:
        if re.search(r'\b' + re.escape(pais) + r'\b', texto, re.IGNORECASE):
            return pais
    return "Origen Variado"

def extraer_proceso(texto):
    for proc in PROCESOS:
        if re.search(r'\b' + re.escape(proc) + r'\b', texto, re.IGNORECASE):
            return proc
    return "Lavado"

def extraer_notas(texto):
    encontradas = [n for n in NOTAS_CATA if re.search(r'\b' + re.escape(n) + r'\b', texto, re.IGNORECASE)]
    return encontradas if encontradas else ["Balanceado"]

def extraer_sca(texto):
    match = re.search(r'\b(SCA\s*|puntos\s*)?([89][0-9](\.[0-9]+)?)\b', texto, re.IGNORECASE)
    if match:
        val = float(match.group(2))
        if 80.0 <= val <= 95.0:
            return val
    return None

def extraer_de_shopify(tostador):
    cafes = []
    endpoint = f"{tostador['url_base']}/products.json?limit=250"
    try:
        res = requests.get(endpoint, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            return None
        data = res.json()
        
        for p in data.get("products", []):
            titulo = p.get("title", "")
            p_type = p.get("product_type", "").lower()
            tags = [t.lower() for t in p.get("tags", [])]
            body_html = p.get("body_html", "") or ""
            body_text = BeautifulSoup(body_html, "html.parser").get_text()
            
            # Filtrar solo productos de café
            full_text = f"{titulo} {p_type} {' '.join(tags)} {body_text}"
            if not any(k in full_text.lower() for k in ["café", "cafe", "coffee", "espresso", "filtro"]):
                continue

            variant = p["variants"][0] if p.get("variants") else {}
            precio = float(variant.get("price", 0))
            disponible = variant.get("available", False)
            imagen = p["images"][0]["src"] if p.get("images") else ""
            link_original = f"{tostador['url_base']}/products/{p.get('handle')}"
            
            cafes.append({
                "n": titulo,
                "r": tostador["nombre"],
                "c": tostador.get("ciudad", ""),
                "u": link_original,
                "aff_url": construir_url_afiliado(link_original, tostador),
                "t": "Espresso" if "espresso" in full_text.lower() else ("Filtro" if "filtro" in full_text.lower() else "Ambos"),
                "o": extraer_origen(full_text),
                "p": extraer_proceso(full_text),
                "nt": extraer_notas(full_text),
                "pr": precio,
                "rt": 4.8,
                "sh": 40.0,
                "mn": 0,
                "s": disponible,
                "sca": extraer_sca(full_text),
                "img": imagen
            })
    except Exception as e:
        print(f"Error procesando Shopify para {tostador['nombre']}: {e}")
        return None
    return cafes

def extraer_de_html(tostador):
    cafes = []
    try:
        res = requests.get(tostador["url_base"], headers=HEADERS, timeout=12)
        if res.status_code != 200:
            return cafes
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Extracción vía datos estructurados Schema.org JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "Product":
                        nombre = item.get("name", "")
                        offers = item.get("offers", {})
                        precio = float(offers.get("price", 0)) if isinstance(offers, dict) else 0.0
                        link = item.get("url", tostador["url_base"])
                        cafes.append({
                            "n": nombre,
                            "r": tostador["nombre"],
                            "c": tostador.get("ciudad", ""),
                            "u": link,
                            "aff_url": construir_url_afiliado(link, tostador),
                            "t": "Ambos",
                            "o": extraer_origen(nombre),
                            "p": extraer_proceso(nombre),
                            "nt": extraer_notas(nombre),
                            "pr": precio,
                            "rt": 4.7,
                            "sh": 40.0,
                            "s": True,
                            "sca": extraer_sca(nombre),
                            "img": item.get("image", "")
                        })
            except Exception:
                continue
    except Exception as e:
        print(f"Error procesando HTML para {tostador['nombre']}: {e}")
    return cafes

def procesar_tostador(tostador):
    cafes = extraer_de_shopify(tostador)
    if cafes is None or len(cafes) == 0:
        cafes = extraer_de_html(tostador)
    return cafes or []

def ejecutar_pipeline():
    tostadores = cargar_tostadores_csv()
    print(f"[*] Iniciando rastreo de {len(tostadores)} tostadores...")
    
    catalogo_total = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(procesar_tostador, t) for t in tostadores]
        for future in as_completed(futures):
            resultado = future.result()
            catalogo_total.extend(resultado)

    for idx, item in enumerate(catalogo_total):
        item["i"] = idx

    with open("cafes.json", "w", encoding="utf-8") as f:
        json.dump(catalogo_total, f, ensure_ascii=False, indent=2)

    with open("estado.json", "w", encoding="utf-8") as f:
        json.dump({
            "fecha": datetime.now().isoformat(),
            "total_cafes": len(catalogo_total),
            "total_tostadores": len(tostadores)
        }, f, ensure_ascii=False, indent=2)

    print(f"[✓] Proceso completado: {len(catalogo_total)} cafés extraídos de {len(tostadores)} tostadores.")

if __name__ == "__main__":
    ejecutar_pipeline()
