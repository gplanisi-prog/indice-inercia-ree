#!/usr/bin/env python3
"""
Índice de Inercia de Red — España (Península)
=============================================
Numerador : Solar FV  +  0.5 × Eólica
Denominador: CC  +  Hidro  +  0.5 × Nuclear  +  resto rotativo

Referencia: apagón ibérico del 28-abr-2025, índice ≈ 5.
Umbral estimado actual (tras mejoras): ≈ 6.

Se ejecuta vía GitHub Actions cada 5 min y genera docs/index.html.
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
MAX_HISTORY  = 288          # 24 h a 5 min

# ── Umbrales (ajustar según evolucione la red) ────────────────────────────────
NIVEL_28A    = 5.0          # índice estimado el día del apagón
NIVEL_ACTUAL = 6.0          # umbral estimado tras mejoras post-28A


# ── 1. OBTENER DATOS ──────────────────────────────────────────────────────────

def fetch_generation():
    """Intenta primero tiempo-real; si falla, usa estructura-generacion."""
    now   = datetime.now(timezone.utc)
    start = now - timedelta(minutes=40)
    fmt   = "%Y-%m-%dT%H:%M"

    headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; IndiceRed/1.0)",
    }

    # Intento 1: tiempo real (resolución ~10 min, solo datos recientes)
    url1 = (
        "https://apidatos.ree.es/es/datos/generacion/generacion-tiempo-real"
        f"?start_date={start.strftime(fmt)}&end_date={now.strftime(fmt)}"
    )
    try:
        r = requests.get(url1, headers=headers, timeout=30)
        if r.ok:
            return r.json()
        print(f"  tiempo-real → HTTP {r.status_code}, probando estructura-generacion...")
    except Exception as e:
        print(f"  tiempo-real → error: {e}, probando estructura-generacion...")

    # Intento 2: estructura-generacion con resolución horaria
    url2 = (
        "https://apidatos.ree.es/es/datos/generacion/estructura-generacion"
        f"?start_date={start.strftime(fmt)}&end_date={now.strftime(fmt)}"
        "&time_trunc=hour"
    )
    r = requests.get(url2, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def latest_by_tech(data: dict) -> dict:
    """Devuelve {tecnología: MW} con el valor más reciente de cada una."""
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

    numerador = (
        g("Solar fotovoltaica") +
        0.5 * g("Eólica")
    )
    denominador = (
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
    return round(numerador / denominador, 3) if denominador > 0 else 0.0


# ── 3. NIVEL DE RIESGO ────────────────────────────────────────────────────────

def risk(idx: float):
    if idx < 2.0:
        return "#27ae60", "Bajo",     "Generación dominada por fuentes rotativas. Red estable."
    if idx < 4.0:
        return "#f1c40f", "Moderado", "Penetración creciente de inversores. Inercia aceptable."
    if idx < NIVEL_28A:
        return "#e67e22", "Alto",     f"Aproximándose al nivel del apagón del 28-A (≈{NIVEL_28A})."
    if idx < NIVEL_ACTUAL:
        return "#e74c3c", "Crítico",  f"En torno al nivel del apagón del 28-A. Riesgo elevado."
    return "#8e44ad",   "Extremo",   f"Por encima del umbral estimado post-mejoras (≈{NIVEL_ACTUAL}). Riesgo muy alto."


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

    # Últimas 6 horas para el gráfico (72 puntos a 5 min)
    chart_slice  = history[-72:]
    chart_labels = json.dumps([h["t"] for h in chart_slice])
    chart_values = json.dumps([h["v"] for h in chart_slice])

    def gv(k, factor=1.0):
        return f"{gen.get(k, 0.0) * factor:.0f}"

    num_total = gen.get("Solar fotovoltaica", 0) + 0.5 * gen.get("Eólica", 0)
    den_total = (gen.get("Ciclo combinado", 0) + gen.get("Hidráulica", 0) +
                 0.5 * gen.get("Nuclear", 0) + gen.get("Carbón", 0) +
                 gen.get("Fuel + Gas", 0) + gen.get("Solar térmica", 0) +
                 gen.get("Cogeneración y resto", 0) + gen.get("Otras renovables", 0) +
                 gen.get("Residuos no renovables", 0))

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
h1{{font-size:1.1rem;color:#888;font-weight:400;margin-bottom:16px}}
.card{{background:#1c1c1e;border-radius:14px;padding:20px;margin-bottom:14px}}
.big{{font-size:5.5rem;font-weight:900;color:{color};line-height:1;letter-spacing:-2px}}
.risk-label{{font-size:1.3rem;color:{color};margin:6px 0 4px}}
.risk-desc{{color:#999;font-size:.9rem;line-height:1.5}}
.ts{{color:#555;font-size:.75rem;margin-top:10px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
td{{padding:5px 8px;border-bottom:1px solid #2a2a2a}}
td:last-child{{text-align:right;font-weight:600;color:#bbb}}
.label-num{{color:#3498db}}
.label-den{{color:#7f8c8d}}
.ref-box{{border-left:3px solid #333;padding:8px 12px;margin:6px 0;font-size:.85rem;color:#888}}
.ref-box b{{color:#bbb}}
.note{{font-size:.8rem;color:#666;line-height:1.6;margin-top:10px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
@media(max-width:500px){{.big{{font-size:4rem}}.grid2{{grid-template-columns:1fr}}}}
</style>
</head>
<body>

<h1>Índice de Inercia de Red · España (Península)</h1>

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
    <div style="color:#3498db;font-size:.75rem;text-transform:uppercase;
                letter-spacing:1px;margin-bottom:10px">Numerador — inversores</div>
    <table>
      <tr><td>Solar fotovoltaica</td><td>{gv("Solar fotovoltaica")} MW</td></tr>
      <tr><td>Eólica &times; 0.5</td><td>{gv("Eólica", .5)} MW</td></tr>
      <tr style="color:#3498db"><td><b>Total</b></td><td><b>{num_total:.0f} MW</b></td></tr>
    </table>
  </div>
  <div class="card">
    <div style="color:#7f8c8d;font-size:.75rem;text-transform:uppercase;
                letter-spacing:1px;margin-bottom:10px">Denominador — rotativos</div>
    <table>
      <tr><td>Ciclo combinado</td><td>{gv("Ciclo combinado")} MW</td></tr>
      <tr><td>Hidráulica</td><td>{gv("Hidráulica")} MW</td></tr>
      <tr><td>Nuclear &times; 0.5</td><td>{gv("Nuclear", .5)} MW</td></tr>
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
  <div class="ref-box"><b>28 de abril de 2025 (apagón ibérico)</b> · índice ≈ {NIVEL_28A}</div>
  <div class="ref-box"><b>Umbral estimado actual</b> (tras mejoras de red) · ≈ {NIVEL_ACTUAL}</div>
  <p class="note">
    El índice mide la proporción de generación desacoplada mecánicamente de la frecuencia de red
    (inversores: solar FV y eólica parcial) frente a la generación que sostiene activamente la inercia
    del sistema (máquinas rotativas síncronas). Valores altos indican baja inercia sistémica y mayor
    vulnerabilidad ante perturbaciones de frecuencia. El bombeo hidráulico se excluye por ser
    consumo neto en el período de medida.
    <br><br>
    Datos: <a href="https://apidatos.ree.es" style="color:#555">apidatos.ree.es</a> ·
    Actualización: cada 5 minutos via GitHub Actions.
  </p>
</div>

<script>
const labels = {chart_labels};
const values = {chart_values};
const ref28a = {NIVEL_28A};
const refAct = {NIVEL_ACTUAL};

new Chart(document.getElementById('chart'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [{{
      label: 'Índice',
      data: values,
      borderColor: '{color}',
      backgroundColor: '{color}18',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }}]
  }},
  options: {{
    responsive: true,
    animation: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' Índice: ' + ctx.parsed.y.toFixed(2)
        }}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{ color:'#555', maxTicksLimit:8 }},
        grid:  {{ color:'#222' }}
      }},
      y: {{
        min: 0,
        ticks: {{ color:'#555' }},
        grid:  {{ color:'#222' }}
      }}
    }}
  }},
  plugins: [{{
    id: 'refLines',
    afterDraw(chart) {{
      const {{ctx, scales: {{y, x}}}} = chart;
      [[ref28a, '#e74c3c', '28-A ≈ ' + ref28a],
       [refAct,  '#8e44ad', 'Umbral ≈ ' + refAct]].forEach(([val, col, lbl]) => {{
        const yPx = y.getPixelForValue(val);
        if (yPx < y.top || yPx > y.bottom) return;
        ctx.save();
        ctx.setLineDash([5,4]);
        ctx.strokeStyle = col;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x.left, yPx);
        ctx.lineTo(x.right, yPx);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = col;
        ctx.font = '11px sans-serif';
        ctx.fillText(lbl, x.right - 80, yPx - 4);
        ctx.restore();
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
    try:
        print("Obteniendo datos de REE...")
        data = fetch_generation()
        gen  = latest_by_tech(data)

        if not gen:
            print("ERROR: respuesta vacía.", file=sys.stderr)
            sys.exit(1)

        idx = calculate_index(gen)
        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Historial
        history = load_history()
        history.append({"t": ts[11:16], "v": idx})
        history = history[-MAX_HISTORY:]
        save_history(history)

        # HTML
        html = generate_html(idx, gen, history, ts)
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(html, encoding="utf-8")

        _, level, _ = risk(idx)
        print(f"OK — Índice: {idx:.3f}  |  Riesgo: {level}  |  {ts} UTC")
        print(f"     Solar FV: {gen.get('Solar fotovoltaica',0):.0f} MW  "
              f"Eólica: {gen.get('Eólica',0):.0f} MW  "
              f"Nuclear: {gen.get('Nuclear',0):.0f} MW  "
              f"CC: {gen.get('Ciclo combinado',0):.0f} MW")
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
