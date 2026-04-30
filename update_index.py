#!/usr/bin/env python3
"""
Índice de Inercia de Red — España (Península)
=============================================
Numerador : Solar FV  +  0.5 × Eólica
Denominador: CC  +  Hidro  +  0.5 × Nuclear  +  resto rotativo

Referencia: apagón ibérico del 28-abr-2025, índice ≈ 5.
Umbral estimado actual (tras mejoras): ≈ 6.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Rutas ─────────────────────────────────────────────────────────────────────
DOCS_DIR     = Path("docs")
HISTORY_FILE = DOCS_DIR / "history.json"
OUTPUT_HTML  = DOCS_DIR / "index.html"
MAX_HISTORY  = 288          # 24 h × 12 muestras/h

# ── Umbrales ──────────────────────────────────────────────────────────────────
NIVEL_28A    = 5.0
NIVEL_ACTUAL = 6.0

# ── Headers que imitan una petición de navegador real ─────────────────────────
# La API de apidatos.ree.es rechaza peticiones sin Referer/Origin de ree.es.
HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer":         "https://www.ree.es/",
    "Origin":          "https://www.ree.es",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


# ── 1. OBTENER DATOS ──────────────────────────────────────────────────────────

def fetch_generation():
    """
    Intenta varios endpoints/parámetros en cascada.
    Devuelve el JSON de la primera respuesta válida.
    """
    now   = datetime.now(timezone.utc)
    fmt   = "%Y-%m-%dT%H:%M"
    today = now.strftime("%Y-%m-%d")

    # Ventanas de tiempo
    start_2h  = (now - timedelta(hours=2)).strftime(fmt)
    end_now   = now.strftime(fmt)
    start_day = f"{today}T00:00"
    end_day   = f"{today}T23:59"

    attempts = [
        # 1. Tiempo real, últimas 2 horas (resolución nativa ~10 min)
        ("tiempo-real 2h",
         f"https://apidatos.ree.es/es/datos/generacion/generacion-tiempo-real"
         f"?start_date={start_2h}&end_date={end_now}"),

        # 2. Estructura-generacion horaria, ventana de 2 horas
        ("estructura hora 2h",
         f"https://apidatos.ree.es/es/datos/generacion/estructura-generacion"
         f"?start_date={start_2h}&end_date={end_now}&time_trunc=hour"),

        # 3. Estructura-generacion horaria, día completo
        ("estructura hora día",
         f"https://apidatos.ree.es/es/datos/generacion/estructura-generacion"
         f"?start_date={start_day}&end_date={end_day}&time_trunc=hour"),

        # 4. Estructura-generacion diaria, día completo (siempre disponible)
        ("estructura día",
         f"https://apidatos.ree.es/es/datos/generacion/estructura-generacion"
         f"?start_date={start_day}&end_date={end_day}&time_trunc=day"),
    ]

    last_error = None
    for label, url in attempts:
        print(f"  Probando {label}: {url}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.ok:
                data = r.json()
                if data.get("included"):
                    print(f"  ✓ OK con '{label}'")
                    return data
                print(f"  Respuesta vacía.")
            else:
                print(f"  HTTP {r.status_code}: {r.text[:120]}")
                last_error = f"HTTP {r.status_code}"
        except Exception as e:
            print(f"  Excepción: {e}")
            last_error = str(e)

    raise RuntimeError(f"Todos los intentos fallaron. Último error: {last_error}")


def latest_by_tech(data: dict) -> dict:
    """Devuelve {tecnología: MW} con el valor más reciente de cada tecnología."""
    result = {}
    for tech in data.get("included", []):
        attrs  = tech.get("attributes", {})
        name   = attrs.get("title", "").strip()
        values = attrs.get("values", [])
        if name and values:
            val = values[-1].get("value") or 0.0
            result[name] = max(0.0, float(val))
    return result


# ── 2. CALCULAR ÍNDICE ────────────────────────────────────────────────────────

def calculate_index(gen: dict) -> float:
    def g(k): return gen.get(k, 0.0)

    num = g("Solar fotovoltaica") + 0.5 * g("Eólica")
    den = (
        g("Ciclo combinado")       +
        g("Hidráulica")            +
        0.5 * g("Nuclear")         +
        g("Carbón")                +
        g("Fuel + Gas")            +
        g("Solar térmica")         +
        g("Cogeneración y resto")  +
        g("Otras renovables")      +
        g("Residuos no renovables")
    )
    return round(num / den, 3) if den > 0 else 0.0


# ── 3. NIVEL DE RIESGO ────────────────────────────────────────────────────────

def risk(idx: float):
    if idx < 2.0:
        return "#27ae60", "Bajo",     "Generación dominada por fuentes rotativas. Red estable."
    if idx < 4.0:
        return "#f1c40f", "Moderado", "Penetración creciente de inversores. Inercia aceptable."
    if idx < NIVEL_28A:
        return "#e67e22", "Alto",     f"Aproximándose al nivel del apagón del 28-A (≈{NIVEL_28A})."
    if idx < NIVEL_ACTUAL:
        return "#e74c3c", "Crítico",  "En torno al nivel del apagón del 28-A. Riesgo elevado."
    return "#8e44ad",   "Extremo",   f"Por encima del umbral estimado post-mejoras (≈{NIVEL_ACTUAL})."


# ── 4. HISTORIAL ──────────────────────────────────────────────────────────────

def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return []


def save_history(history):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, separators=(",", ":")))


# ── 5. GENERAR HTML ───────────────────────────────────────────────────────────

def generate_html(idx: float, gen: dict, history: list, ts: str) -> str:
    color, level, desc = risk(idx)

    chart_slice  = history[-72:]
    chart_labels = json.dumps([h["t"] for h in chart_slice])
    chart_values = json.dumps([h["v"] for h in chart_slice])

    def gv(k, f=1.0): return f"{gen.get(k, 0.0) * f:.0f}"

    num_total = gen.get("Solar fotovoltaica", 0) + 0.5 * gen.get("Eólica", 0)
    den_total = sum(gen.get(k, 0) for k in [
        "Ciclo combinado", "Hidráulica", "Carbón", "Fuel + Gas",
        "Solar térmica", "Cogeneración y resto", "Otras renovables",
        "Residuos no renovables"
    ]) + 0.5 * gen.get("Nuclear", 0)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Índice de Inercia · Red Eléctrica España</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#111;color:#ddd;padding:16px;max-width:860px;margin:0 auto}}
h1{{font-size:1.05rem;color:#666;font-weight:400;margin-bottom:16px;letter-spacing:.5px}}
.card{{background:#1c1c1e;border-radius:14px;padding:20px;margin-bottom:14px}}
.big{{font-size:5.5rem;font-weight:900;color:{color};line-height:1;letter-spacing:-2px}}
.risk-label{{font-size:1.25rem;color:{color};margin:8px 0 4px}}
.risk-desc{{color:#999;font-size:.9rem}}
.ts{{color:#444;font-size:.75rem;margin-top:10px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
td{{padding:5px 6px;border-bottom:1px solid #252525}}
td:last-child{{text-align:right;font-weight:600;color:#bbb}}
.ref-box{{border-left:3px solid #2a2a2a;padding:8px 12px;margin:6px 0;
          font-size:.85rem;color:#777}}
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
  <div class="ts">Actualizado: {ts} UTC &nbsp;·&nbsp; refresco automático cada 5 min</div>
</div>

<div class="card">
  <canvas id="chart"></canvas>
</div>

<div class="grid2">
  <div class="card">
    <div class="sh" style="color:#3498db">Numerador — inversores</div>
    <table>
      <tr><td>Solar fotovoltaica</td><td>{gv("Solar fotovoltaica")} MW</td></tr>
      <tr><td>Eólica &times; 0.5</td><td>{gv("Eólica", .5)} MW</td></tr>
      <tr style="color:#3498db"><td><b>Total</b></td><td><b>{num_total:.0f} MW</b></td></tr>
    </table>
  </div>
  <div class="card">
    <div class="sh" style="color:#7f8c8d">Denominador — rotativos</div>
    <table>
      <tr><td>Ciclo combinado</td><td>{gv("Ciclo combinado")} MW</td></tr>
      <tr><td>Hidráulica</td><td>{gv("Hidráulica")} MW</td></tr>
      <tr><td>Nuclear &times; 0.5</td><td>{gv("Nuclear",.5)} MW</td></tr>
      <tr><td>Carbón</td><td>{gv("Carbón")} MW</td></tr>
      <tr><td>Fuel + Gas</td><td>{gv("Fuel + Gas")} MW</td></tr>
      <tr><td>Solar térmica</td><td>{gv("Solar térmica")} MW</td></tr>
      <tr><td>Cogeneración y resto</td><td>{gv("Cogeneración y resto")} MW</td></tr>
      <tr><td>Otras renovables</td><td>{gv("Otras renovables")} MW</td></tr>
      <tr><td>Residuos no renov.</td><td>{gv("Residuos no renovables")} MW</td></tr>
      <tr style="color:#7f8c8d"><td><b>Total</b></td><td><b>{den_total:.0f} MW</b></td></tr>
    </table>
  </div>
</div>

<div class="card">
  <div class="ref-box"><b>28 de abril de 2025 (apagón ibérico)</b> &nbsp;·&nbsp; índice ≈ {NIVEL_28A}</div>
  <div class="ref-box"><b>Umbral estimado actual</b> (tras mejoras de red) &nbsp;·&nbsp; ≈ {NIVEL_ACTUAL}</div>
  <p class="note">
    El índice mide la proporción de generación desacoplada mecánicamente de la frecuencia de red
    (inversores: solar FV y eólica parcial) frente a la generación que sostiene la inercia del sistema
    (máquinas rotativas síncronas). Valores altos indican baja inercia sistémica y mayor vulnerabilidad
    ante perturbaciones de frecuencia. La hidráulica de bombeo se excluye por ser consumo neto.
    <br><br>
    Datos: <a href="https://apidatos.ree.es" style="color:#444">apidatos.ree.es</a> ·
    Código: <a href="https://github.com/gplanisi-prog/indice-inercia-ree" style="color:#444">GitHub</a> ·
    Actualización cada 5 min vía GitHub Actions.
  </p>
</div>

<script>
new Chart(document.getElementById('chart'), {{
  type: 'line',
  data: {{
    labels: {chart_labels},
    datasets: [{{
      data: {chart_values},
      borderColor: '{color}',
      backgroundColor: '{color}18',
      fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2
    }}]
  }},
  options: {{
    responsive: true, animation: false,
    plugins: {{ legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: c => ' Índice: ' + c.parsed.y.toFixed(2) }} }} }},
    scales: {{
      x: {{ ticks: {{ color:'#444', maxTicksLimit:8 }}, grid: {{ color:'#1a1a1a' }} }},
      y: {{ min: 0, ticks: {{ color:'#444' }}, grid: {{ color:'#1a1a1a' }} }}
    }}
  }},
  plugins: [{{
    id: 'refs',
    afterDraw(chart) {{
      const {{ctx, scales: {{x, y}}}} = chart;
      [{{"v":{NIVEL_28A},"c":"#e74c3c","l":"28-A ≈{NIVEL_28A}}"},
       {{"v":{NIVEL_ACTUAL},"c":"#8e44ad","l":"Umbral ≈{NIVEL_ACTUAL}"}}].forEach(r => {{
        const yp = y.getPixelForValue(r.v);
        if (yp < y.top || yp > y.bottom) return;
        ctx.save(); ctx.setLineDash([5,4]); ctx.strokeStyle=r.c;
        ctx.lineWidth=1; ctx.beginPath();
        ctx.moveTo(x.left,yp); ctx.lineTo(x.right,yp); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle=r.c; ctx.font='11px sans-serif';
        ctx.fillText(r.l, x.right-90, yp-4); ctx.restore();
      }});
    }}
  }}]
}});
</script>
</body>
</html>
"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Obteniendo datos de REE...")
    try:
        data = fetch_generation()
        gen  = latest_by_tech(data)

        if not gen:
            print("ERROR: respuesta sin tecnologías.", file=sys.stderr)
            sys.exit(1)

        idx = calculate_index(gen)
        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        history = load_history()
        history.append({"t": ts[11:16], "v": idx})
        history = history[-MAX_HISTORY:]
        save_history(history)

        html = generate_html(idx, gen, history, ts)
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(html, encoding="utf-8")

        _, level, _ = risk(idx)
        print(f"OK — Índice: {idx:.3f}  |  {level}  |  {ts} UTC")
        print(f"     FV:{gen.get('Solar fotovoltaica',0):.0f}  "
              f"Eólica:{gen.get('Eólica',0):.0f}  "
              f"Nuclear:{gen.get('Nuclear',0):.0f}  "
              f"CC:{gen.get('Ciclo combinado',0):.0f}  MW")
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)