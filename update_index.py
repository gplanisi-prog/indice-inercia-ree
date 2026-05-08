#!/usr/bin/env python3
"""
Índice de Inercia de Red — España (Península)
=============================================
Numerador : Solar FV  +  0.5 × Eólica
Denominador: CC  +  Hidro  +  0.5 × Nuclear  +  resto rotativo

Fuente primaria : API de ESIOS (api.esios.ree.es) — potencia instantánea ~10 min
Fuente de respaldo: apidatos.ree.es — MWh/hora (si ESIOS no responde)

Token ESIOS: variable de entorno ESIOS_TOKEN (GitHub Actions secret)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Rutas ─────────────────────────────────────────────────────────────────────
DOCS_DIR     = Path("docs")
HISTORY_FILE = DOCS_DIR / "history.json"
OUTPUT_HTML  = DOCS_DIR / "index.html"
MAX_HISTORY  = 288

# ── Umbrales ──────────────────────────────────────────────────────────────────
NIVEL_28A    = 5.0
NIVEL_ACTUAL = 6.0

# ── Indicadores ESIOS de generación real por tecnología (Península) ───────────
# Serie "Generación T.Real" — IDs verificados con discover_indicators.py
# Nota: 552 "Solar" agrega FV + térmica (solo disponible de día).
#        555 "Resto" incluye cogeneración, residuos y otras renovables.
ESIOS_INDICATORS = {
    "Hidráulica":           546,
    "Carbón":               547,
    "Fuel + Gas":           548,
    "Nuclear":              549,
    "Ciclo combinado":      550,
    "Eólica":               551,
    "Solar fotovoltaica":   552,   # en realidad Solar total (FV + térmica)
    "Cogeneración y resto": 555,   # Resto: cogen. + residuos + otras renovables
}

ESIOS_BASE   = "https://api.esios.ree.es"

# ── Headers ESIOS ─────────────────────────────────────────────────────────────
def esios_headers(token: str) -> dict:
    return {
        "Authorization":  f'Token token="{token}"',
        "x-api-key":      token,
        "Accept":         "application/json; application/vnd.esios-api-v2+json",
        "Content-Type":   "application/json",
    }

# ── Headers apidatos.ree.es (imitar navegador) ────────────────────────────────
REDATA_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer":         "https://www.ree.es/",
    "Origin":          "https://www.ree.es",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


# ── 1a. OBTENER DATOS — ESIOS ─────────────────────────────────────────────────

def fetch_esios(token: str):
    """
    Consulta ESIOS para cada tecnología. Devuelve el último valor MW de cada una.
    Pide los últimos 30 minutos con resolución de 10 minutos.
    """
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    hdrs  = esios_headers(token)

    gen     = {}
    data_ts = ""

    for tech, ind_id in ESIOS_INDICATORS.items():
        url = (f"{ESIOS_BASE}/indicators/{ind_id}"
               f"?start_date={start}&end_date={end}&time_trunc=ten_minutes")
        try:
            r = requests.get(url, headers=hdrs, timeout=20)
            if not r.ok:
                print(f"  ESIOS {ind_id} ({tech}): HTTP {r.status_code}")
                continue
            values = (r.json()
                       .get("indicator", {})
                       .get("values", []))
            if values:
                last   = values[-1]
                val    = last.get("value") or 0.0
                ts     = last.get("datetime", "")
                gen[tech] = max(0.0, float(val))
                if ts > data_ts:
                    data_ts = ts
        except Exception as e:
            print(f"  ESIOS {ind_id} ({tech}): {e}")

    if len(gen) < 3:          # mínimo: nuclear + CC + eólica o hidro
        raise RuntimeError(f"ESIOS solo devolvió {len(gen)} tecnologías (mínimo 3).")

    return gen, data_ts, "MW"


# ── 1b. OBTENER DATOS — apidatos.ree.es (respaldo) ───────────────────────────

def fetch_redata():
    """Usa apidatos.ree.es con estructura-generacion a horas completas."""
    now       = datetime.now(timezone.utc)
    hora      = now.replace(minute=0, second=0, microsecond=0)
    start_3h  = (hora - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    end_hora  = hora.strftime("%Y-%m-%dT%H:%M")
    today     = now.strftime("%Y-%m-%d")

    attempts = [
        ("estructura 3h",
         f"https://apidatos.ree.es/es/datos/generacion/estructura-generacion"
         f"?start_date={start_3h}&end_date={end_hora}&time_trunc=hour"),
        ("estructura día",
         f"https://apidatos.ree.es/es/datos/generacion/estructura-generacion"
         f"?start_date={today}T00:00&end_date={end_hora}&time_trunc=hour"),
    ]

    for label, url in attempts:
        print(f"  Respaldo: {label}...")
        r = requests.get(url, headers=REDATA_HEADERS, timeout=30)
        if r.ok:
            data = r.json()
            if data.get("included"):
                gen, data_ts, mag = {}, "", "MWh/h"
                for tech in data["included"]:
                    attrs  = tech.get("attributes", {})
                    name   = attrs.get("title", "").strip()
                    vals   = attrs.get("values", [])
                    mag    = attrs.get("magnitude", "MWh/h") or "MWh/h"
                    if name and vals:
                        last = vals[-1]
                        gen[name] = max(0.0, float(last.get("value") or 0))
                        ts = last.get("datetime", "")
                        if ts > data_ts:
                            data_ts = ts
                return gen, data_ts, mag
        print(f"  HTTP {r.status_code}")

    raise RuntimeError("apidatos.ree.es también falló.")


# ── 1. OBTENER DATOS (con fallback) ──────────────────────────────────────────

def fetch_generation():
    # Buscar token: 1) variable de entorno, 2) fichero .env local (solo para pruebas)
    token = os.environ.get("ESIOS_TOKEN", "").strip()
    if not token:
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ESIOS_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if token:
        print("Intentando ESIOS (potencia instantánea MW)...")
        try:
            gen, data_ts, mag = fetch_esios(token)
            print(f"  ✓ ESIOS OK — {len(gen)} tecnologías")
            return gen, data_ts, mag, "ESIOS · MW instantáneo ~10 min"
        except Exception as e:
            print(f"  ESIOS falló: {e} — usando respaldo")
    else:
        print("Sin token ESIOS — usando apidatos.ree.es")

    gen, data_ts, mag = fetch_redata()
    return gen, data_ts, mag, f"apidatos.ree.es · {mag}"


# ── 2. CALCULAR ÍNDICE ────────────────────────────────────────────────────────

def calculate_index(gen: dict) -> float:
    def g(k): return gen.get(k, 0.0)
    num = g("Solar fotovoltaica") + 0.5 * g("Eólica")   # "Solar fotovoltaica" = Solar total
    den = (g("Ciclo combinado") + g("Hidráulica") + 0.5 * g("Nuclear") +
           g("Carbón") + g("Fuel + Gas") + g("Cogeneración y resto"))
    return round(num / den, 3) if den > 0 else 0.0


# ── 3. NIVEL DE RIESGO ────────────────────────────────────────────────────────

def risk(idx: float):
    if idx < 2.0:  return "#27ae60", "Bajo",    "Generación dominada por fuentes rotativas. Red estable."
    if idx < 4.0:  return "#f1c40f", "Moderado","Penetración creciente de inversores. Inercia aceptable."
    if idx < NIVEL_28A:   return "#e67e22","Alto",    f"Aproximándose al nivel del apagón del 28-A (≈{NIVEL_28A})."
    if idx < NIVEL_ACTUAL:return "#e74c3c","Crítico", "En torno al nivel del apagón del 28-A. Riesgo elevado."
    return "#8e44ad","Extremo", f"Por encima del umbral estimado post-mejoras (≈{NIVEL_ACTUAL})."


# ── 4. HISTORIAL ──────────────────────────────────────────────────────────────

def load_history():
    if HISTORY_FILE.exists():
        try: return json.loads(HISTORY_FILE.read_text())
        except: pass
    return []

def save_history(h):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(h, separators=(",",":")))

def format_ts(iso: str) -> str:
    if not iso: return "—"
    try:
        dt = datetime.fromisoformat(iso[:19])
        return dt.strftime("%d/%m/%Y %H:%M")
    except: return iso[:16]


# ── 5. GENERAR HTML ───────────────────────────────────────────────────────────

def generate_html(idx, gen, history, run_ts, data_ts, fuente, magnitud):
    color, level, desc = risk(idx)
    sl = history[-72:]
    chart_labels = json.dumps([h["t"] for h in sl])
    chart_values = json.dumps([h["v"] for h in sl])

    def gv(k, f=1.0):
        v = gen.get(k, 0.0) * f
        return f"{v:,.0f}".replace(",", ".")

    num_total = gen.get("Solar fotovoltaica", 0) + 0.5 * gen.get("Eólica", 0)
    den_total = (sum(gen.get(k,0) for k in ["Ciclo combinado","Hidráulica","Carbón",
                 "Fuel + Gas","Cogeneración y resto"])
                 + 0.5 * gen.get("Nuclear", 0))

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Índice de Inercia · Red Eléctrica España</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#111;color:#ddd;padding:16px;max-width:860px;margin:0 auto}}
h1{{font-size:1.05rem;color:#666;font-weight:400;margin-bottom:16px;letter-spacing:.5px}}
.card{{background:#1c1c1e;border-radius:14px;padding:20px;margin-bottom:14px}}
.big{{font-size:5.5rem;font-weight:900;color:{color};line-height:1;letter-spacing:-2px}}
.risk-label{{font-size:1.25rem;color:{color};margin:8px 0 4px}}
.risk-desc{{color:#999;font-size:.9rem}}
.ts-block{{margin-top:12px;font-size:.78rem;line-height:1.8;color:#555}}
.ts-block b{{color:#888}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
td{{padding:5px 6px;border-bottom:1px solid #252525}}
td:last-child{{text-align:right;font-weight:600;color:#bbb;font-variant-numeric:tabular-nums}}
.ref-box{{border-left:3px solid #2a2a2a;padding:8px 12px;margin:6px 0;font-size:.85rem;color:#777}}
.ref-box b{{color:#aaa}}
.note{{font-size:.8rem;color:#555;line-height:1.6;margin-top:12px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.sh{{font-size:.72rem;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}}
@media(max-width:520px){{.big{{font-size:4rem}}.grid2{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>ÍNDICE DE INERCIA DE RED · ESPAÑA (PENÍNSULA)</h1>
<div class="card">
  <div class="big">{idx:.2f}</div>
  <div class="risk-label">● {level}</div>
  <div class="risk-desc">{desc}</div>
  <div class="ts-block">
    <b>Datos de:</b> {data_ts} (hora peninsular)<br>
    <b>Script:</b> {run_ts} UTC &nbsp;·&nbsp; <b>Fuente:</b> {fuente} &nbsp;·&nbsp; <b>Unidad:</b> {magnitud}
  </div>
</div>
<div class="card"><canvas id="chart"></canvas></div>
<div class="grid2">
  <div class="card">
    <div class="sh" style="color:#3498db">Numerador — inversores</div>
    <table>
      <tr><td>Solar fotovoltaica</td><td>{gv("Solar fotovoltaica")} {magnitud}</td></tr>
      <tr><td>Eólica &times; 0.5</td><td>{gv("Eólica",.5)} {magnitud}</td></tr>
      <tr style="color:#3498db"><td><b>Total</b></td><td><b>{num_total:,.0f} {magnitud}</b></td></tr>
    </table>
  </div>
  <div class="card">
    <div class="sh" style="color:#7f8c8d">Denominador — rotativos</div>
    <table>
      <tr><td>Ciclo combinado</td><td>{gv("Ciclo combinado")} {magnitud}</td></tr>
      <tr><td>Hidráulica</td><td>{gv("Hidráulica")} {magnitud}</td></tr>
      <tr><td>Nuclear &times; 0.5</td><td>{gv("Nuclear",.5)} {magnitud}</td></tr>
      <tr><td>Carbón</td><td>{gv("Carbón")} {magnitud}</td></tr>
      <tr><td>Fuel + Gas</td><td>{gv("Fuel + Gas")} {magnitud}</td></tr>
      <tr><td>Cogen. + resto</td><td>{gv("Cogeneración y resto")} {magnitud}</td></tr>
      <tr style="color:#7f8c8d"><td><b>Total</b></td><td><b>{den_total:,.0f} {magnitud}</b></td></tr>
    </table>
  </div>
</div>
<div class="card">
  <div class="ref-box"><b>28 de abril de 2025 (apagón ibérico)</b> &nbsp;·&nbsp; índice ≈ {NIVEL_28A}</div>
  <div class="ref-box"><b>Umbral estimado actual</b> (tras mejoras de red) &nbsp;·&nbsp; ≈ {NIVEL_ACTUAL}</div>
  <p class="note">El índice mide la proporción de generación desacoplada mecánicamente de la frecuencia
  de red (inversores: solar FV y eólica parcial) frente a la generación que sostiene la inercia del sistema
  (máquinas rotativas síncronas). Valores altos indican baja inercia y mayor vulnerabilidad ante
  perturbaciones de frecuencia. La hidráulica de bombeo se excluye por ser consumo neto.<br><br>
  Datos: <a href="https://api.esios.ree.es" style="color:#444">ESIOS</a> /
  <a href="https://apidatos.ree.es" style="color:#444">REData</a> ·
  Código: <a href="https://github.com/gplanisi-prog/indice-inercia-ree" style="color:#444">GitHub</a> ·
  Actualización cada ~10 min vía GitHub Actions.</p>
</div>
<script>
new Chart(document.getElementById('chart'),{{
  type:'line',
  data:{{labels:{chart_labels},datasets:[{{data:{chart_values},borderColor:'{color}',
    backgroundColor:'{color}18',fill:true,tension:0.3,pointRadius:0,borderWidth:2}}]}},
  options:{{responsive:true,animation:false,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>' Índice: '+c.parsed.y.toFixed(2)}}}}}},
    scales:{{x:{{ticks:{{color:'#444',maxTicksLimit:8}},grid:{{color:'#1a1a1a'}}}},
             y:{{min:0,ticks:{{color:'#444'}},grid:{{color:'#1a1a1a'}}}}}}}},
  plugins:[{{id:'refs',afterDraw(chart){{
    const {{ctx,scales:{{x,y}}}}=chart;
    [{{"v":{NIVEL_28A},"c":"#e74c3c","l":"28-A ≈{NIVEL_28A}"}},
     {{"v":{NIVEL_ACTUAL},"c":"#8e44ad","l":"Umbral ≈{NIVEL_ACTUAL}"}}].forEach(r=>{{
      const yp=y.getPixelForValue(r.v);
      if(yp<y.top||yp>y.bottom)return;
      ctx.save();ctx.setLineDash([5,4]);ctx.strokeStyle=r.c;ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(x.left,yp);ctx.lineTo(x.right,yp);ctx.stroke();
      ctx.setLineDash([]);ctx.fillStyle=r.c;ctx.font='11px sans-serif';
      ctx.fillText(r.l,x.right-90,yp-4);ctx.restore();
    }});
  }}}}]
}});
</script>
</body></html>
"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Obteniendo datos de generación...")
    try:
        gen, data_ts, magnitud, fuente = fetch_generation()
        if not gen:
            print("ERROR: sin datos.", file=sys.stderr); sys.exit(1)

        idx    = calculate_index(gen)
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        data_ts_fmt = format_ts(data_ts)

        history = load_history()
        history.append({"t": data_ts_fmt[-5:] or run_ts[11:16], "v": idx})
        history = history[-MAX_HISTORY:]
        save_history(history)

        html = generate_html(idx, gen, history, run_ts, data_ts_fmt, fuente, magnitud)
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(html, encoding="utf-8")

        _, level, _ = risk(idx)
        print(f"OK — Índice: {idx:.3f}  |  {level}  |  datos: {data_ts_fmt}")
        print(f"     FV:{gen.get('Solar fotovoltaica',0):.0f}  "
              f"Eólica:{gen.get('Eólica',0):.0f}  "
              f"Nuclear:{gen.get('Nuclear',0):.0f}  "
              f"CC:{gen.get('Ciclo combinado',0):.0f}  {magnitud}")
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
