"""
Bot de avisos de fútbol chileno por WhatsApp (CallMeBot).

Modos:
  python bot.py resumen          -> manda el resumen del día (partidos de hoy, mañana y ayer)
  python bot.py envivo           -> vigila los partidos y avisa entretiempo y final
  python bot.py auto             -> lo que usa GitHub Actions: manda el resumen si toca
                                    y luego vigila partidos en vivo
  python bot.py prueba "texto"   -> manda un mensaje de prueba
  python bot.py ver              -> muestra en pantalla lo que mandaría (no envía nada)

  python bot.py grupos           -> lista los grupos de WhatsApp (para saber el CHAT_ID)

Envío por Green-API (variables de entorno):
  GREEN_API_URL, GREEN_ID, GREEN_TOKEN  -> datos de la instancia
  CHAT_ID                               -> grupo o chat destino (ej. 1203...@g.us)
Alternativa CallMeBot: RECIPIENTS = +56911111111:APIKEY1,+56922222222:APIKEY2
"""

import csv
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent

# Para probar en el PC: claves en secretos.env (KEY=valor). En GitHub se usan los "secrets".
if (BASE / "secretos.env").exists():
    for _linea in (BASE / "secretos.env").read_text(encoding="utf-8-sig").splitlines():
        if "=" in _linea and not _linea.strip().startswith("#"):
            _k, _v = _linea.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(CONFIG["zona_horaria"])
ARCHIVO_ESTADO = BASE / "estado.json"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/scoreboard?dates={fecha}"

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
SIMULAR = False  # True en modo "ver": imprime en vez de enviar


# ----------------------------------------------------------------- utilidades

def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


FAVORITOS = [normalizar(f) for f in CONFIG["favoritos"]]


def es_favorito(nombre):
    n = normalizar(nombre)
    return any(f == n or f in n for f in FAVORITOS)


def ahora():
    return datetime.now(TZ)


def http_get(url, intentos=3):
    ultimo_error = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (futbol-familiar)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code < 500:  # errores 4xx no se arreglan reintentando
                raise
            ultimo_error = e
            time.sleep(5 * (i + 1))
        except Exception as e:  # noqa: BLE001
            ultimo_error = e
            time.sleep(5 * (i + 1))
    raise ultimo_error


def cargar_estado():
    if ARCHIVO_ESTADO.exists():
        try:
            return json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"resumen_enviado": "", "partidos": {}}


def guardar_estado(estado):
    # Borra partidos de hace más de 10 días para que el archivo no crezca
    limite = (ahora() - timedelta(days=10)).strftime("%Y-%m-%d")
    estado["partidos"] = {k: v for k, v in estado["partidos"].items() if v.get("fecha", "9999") >= limite}
    ARCHIVO_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ WhatsApp

def destinatarios():
    crudo = os.environ.get("RECIPIENTS", "").strip()
    lista = []
    for par in crudo.split(","):
        par = par.strip()
        if not par:
            continue
        telefono, _, apikey = par.rpartition(":")
        lista.append((telefono.strip(), apikey.strip()))
    return lista


def green_api(metodo, cuerpo=None):
    """Llama a Green-API. Necesita GREEN_API_URL, GREEN_ID y GREEN_TOKEN."""
    url = (f"{os.environ['GREEN_API_URL'].rstrip('/')}/waInstance{os.environ['GREEN_ID'].strip()}"
           f"/{metodo}/{os.environ['GREEN_TOKEN'].strip()}")
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def enviar(texto):
    if SIMULAR:
        print("\n----- MENSAJE -----\n" + texto + "\n-------------------")
        return True
    if os.environ.get("GREEN_TOKEN"):
        todo_ok = True
        for chat in [c.strip() for c in os.environ.get("CHAT_ID", "").split(",") if c.strip()]:
            try:
                r = green_api("sendMessage", {"chatId": chat, "message": texto})
                ok = bool(r and r.get("idMessage"))
                print(f"Envío a {chat[-12:]}: {'OK' if ok else 'FALLÓ'} | {r}")
            except Exception as e:  # noqa: BLE001
                print(f"Envío a {chat[-12:]} FALLÓ: {e}")
                ok = False
            todo_ok &= ok
        return todo_ok
    lista = destinatarios()
    if not lista:
        print("ERROR: falta la variable RECIPIENTS (telefono:apikey)")
        return False
    todo_ok = True
    for telefono, apikey in lista:
        url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
            {"phone": telefono, "text": texto, "apikey": apikey}
        )
        try:
            respuesta = http_get(url, intentos=2)
            ok = "error" not in respuesta.lower()
            print(f"Envío a ...{telefono[-4:]}: {'OK' if ok else 'FALLÓ'} | {respuesta[:150]!r}")
            todo_ok &= ok
        except Exception as e:  # noqa: BLE001
            print(f"Envío a ...{telefono[-4:]} FALLÓ: {e}")
            todo_ok = False
        time.sleep(4)  # CallMeBot pide no mandar ráfagas
    return todo_ok


# ------------------------------------------------------------------- datos

_CACHE = {}


def leer_espn(liga, dia):
    """Lee el marcador de ESPN; guarda la respuesta 60 s para no repetir consultas."""
    clave = (liga, dia)
    if clave in _CACHE and time.time() - _CACHE[clave][0] < 60:
        return _CACHE[clave][1]
    datos = json.loads(http_get(ESPN.format(liga=liga, fecha=dia.strftime("%Y%m%d"))))
    _CACHE[clave] = (time.time(), datos)
    return datos


def partidos_de(liga, nombre_torneo, dia):
    """Partidos de una liga cuyo inicio (hora Chile) cae en 'dia'.
    ESPN agrupa por fecha de EE.UU., así que se consulta el día y el siguiente."""
    vistos, salida = set(), []
    for d in (dia, dia + timedelta(days=1)):
        try:
            datos = leer_espn(liga, d)
        except Exception as e:  # noqa: BLE001
            print(f"No se pudo leer {liga} {d}: {e}")
            continue
        for ev in datos.get("events", []):
            if ev["id"] in vistos:
                continue
            vistos.add(ev["id"])
            inicio = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(TZ)
            if inicio.date() != dia:
                continue
            comp = ev["competitions"][0]
            equipos = {c["homeAway"]: c for c in comp["competitors"]}
            local, visita = equipos.get("home"), equipos.get("away")
            if not local or not visita:
                continue
            estado = ev["status"]["type"]
            salida.append({
                "id": f"{liga}:{ev['id']}",
                "torneo": nombre_torneo,
                "inicio": inicio,
                "local": local["team"]["displayName"],
                "visita": visita["team"]["displayName"],
                "gl": local.get("score", "0"),
                "gv": visita.get("score", "0"),
                "estado": estado.get("name", ""),
                "fase": estado.get("state", ""),       # pre / in / post
                "detalle": estado.get("shortDetail", ""),
                "reloj": ev["status"].get("displayClock", ""),
            })
    return salida


def partidos_manuales(dia):
    """Partidos que se anotan a mano en partidos_manuales.csv (p. ej. Fernández Vial)."""
    archivo = BASE / "partidos_manuales.csv"
    if not archivo.exists():
        return []
    salida = []
    with archivo.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            try:
                fecha = datetime.strptime(fila["fecha"].strip(), "%Y-%m-%d").date()
            except (ValueError, KeyError, AttributeError):
                continue
            if fecha != dia:
                continue
            hora = (fila.get("hora") or "").strip() or "00:00"
            inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            resultado = (fila.get("resultado") or "").strip()
            gl, _, gv = resultado.partition("-")
            salida.append({
                "id": f"manual:{fecha}:{fila['local']}:{fila['visita']}",
                "torneo": (fila.get("torneo") or "").strip() or "Otro torneo",
                "inicio": inicio,
                "local": fila["local"].strip(),
                "visita": fila["visita"].strip(),
                "gl": gl.strip(), "gv": gv.strip(),
                "estado": "STATUS_FULL_TIME" if resultado else "STATUS_SCHEDULED",
                "fase": "post" if resultado else "pre",
                "detalle": "", "reloj": "",
                "manual": True,
            })
    return salida


def del_dia(dia, incluir_principal=True):
    """(partidos de Primera del día, partidos de favoritos en otras competencias)."""
    lp = CONFIG["liga_principal"]
    principal = partidos_de(lp["codigo"], lp["nombre"], dia) if incluir_principal else []
    otros = []
    for codigo, nombre in CONFIG["otras_competencias_favoritos"].items():
        otros += [p for p in partidos_de(codigo, nombre, dia) if tiene_favorito(p)]
    otros += partidos_manuales(dia)
    principal.sort(key=lambda p: p["inicio"])
    otros.sort(key=lambda p: p["inicio"])
    return principal, otros


def tiene_favorito(p):
    return es_favorito(p["local"]) or es_favorito(p["visita"])


# ---------------------------------------------------------------- formato

def estrella(p):
    return "⭐ " if tiene_favorito(p) else ""


def linea_programado(p, con_torneo=False):
    t = f" ({p['torneo']})" if con_torneo else ""
    return f"{estrella(p)}{p['inicio']:%H:%M}  {p['local']} vs {p['visita']}{t}"


def marcador(p):
    return f"{p['local']} {p['gl']}-{p['gv']} {p['visita']}"


def suspendido(p):
    return any(x in p["estado"] for x in ("POSTPONED", "CANCELED", "ABANDONED", "SUSPENDED", "DELAYED"))


def texto_resumen(hoy):
    manana, ayer = hoy + timedelta(days=1), hoy - timedelta(days=1)
    prim_hoy, otros_hoy = del_dia(hoy)
    prim_man, otros_man = del_dia(manana)
    prim_ayer, otros_ayer = del_dia(ayer)

    partes = []

    # 1) Partidos de hoy en Primera División
    if prim_hoy:
        partes.append(f"⚽ *Hoy {DIAS[hoy.weekday()]} {hoy:%d/%m} – {CONFIG['liga_principal']['nombre']}*")
        partes += [linea_programado(p) for p in prim_hoy]
    # Favoritos hoy en otras competencias
    if otros_hoy:
        partes.append("\n🏆 *Hoy, otros torneos*")
        partes += [linea_programado(p, con_torneo=True) for p in otros_hoy]

    # 2) Aviso un día antes (favoritos)
    fav_man = [p for p in prim_man + otros_man if tiene_favorito(p)]
    if fav_man:
        partes.append(f"\n📅 *Mañana {DIAS[manana.weekday()]} {manana:%d/%m}*")
        partes += [linea_programado(p, con_torneo=True) for p in fav_man]

    # 3) Aviso un día después (resultados de favoritos)
    fav_ayer = [p for p in prim_ayer + otros_ayer if tiene_favorito(p)]
    if fav_ayer:
        partes.append("\n🔙 *Resultados de ayer*")
        for p in fav_ayer:
            if suspendido(p):
                partes.append(f"⭐ {p['local']} vs {p['visita']}: suspendido")
            elif p["fase"] == "post":
                partes.append(f"⭐ {marcador(p)} ({p['torneo']})")
            else:
                partes.append(f"⭐ {p['local']} vs {p['visita']}: sin resultado aún")

    return "\n".join(partes).strip()


# ------------------------------------------------------------------ modos

def modo_resumen(estado, forzar=False):
    hoy = ahora().date()
    if not forzar and estado.get("resumen_enviado") == hoy.isoformat():
        print("Resumen de hoy ya enviado.")
        return
    texto = texto_resumen(hoy)
    if not texto:
        print("Hoy no hay nada que avisar; no se envía resumen.")
        estado["resumen_enviado"] = hoy.isoformat()
        return
    if enviar(texto) and not SIMULAR:
        estado["resumen_enviado"] = hoy.isoformat()


def vigilar_una_vez(estado):
    """Revisa partidos de hoy (y de ayer, por si alguno terminó después de medianoche).
    Devuelve True si queda algo por vigilar en la próxima hora."""
    hoy = ahora().date()
    candidatos = []
    for dia in (hoy - timedelta(days=1), hoy):
        principal, otros = del_dia(dia)
        if CONFIG["avisar_entretiempo_y_final"] == "favoritos":
            principal = [p for p in principal if tiene_favorito(p)]
        candidatos += principal + [p for p in otros if not p.get("manual")]

    entretiempos, finales = [], []
    queda_algo = False
    for p in candidatos:
        reg = estado["partidos"].setdefault(p["id"], {"fecha": p["inicio"].date().isoformat()})
        if p["estado"] == "STATUS_HALFTIME" and not reg.get("et") and not reg.get("fin"):
            entretiempos.append(p)
            reg["et"] = True
        elif p["fase"] == "post" and not suspendido(p) and not reg.get("fin"):
            # Solo avisar finales recientes (evita avisos viejos si el bot estuvo apagado)
            if ahora() - p["inicio"] < timedelta(hours=5):
                finales.append(p)
            reg["fin"] = True
        if p["fase"] == "in" or (p["fase"] == "pre" and not suspendido(p)
                                 and p["inicio"] - ahora() < timedelta(minutes=65)):
            queda_algo = True

    bloques = []
    if entretiempos:
        bloques.append("⏸️ *Entretiempo*\n" + "\n".join(estrella(p) + marcador(p) for p in entretiempos))
    if finales:
        lineas = []
        for p in finales:
            extra = f" ({p['detalle']})" if p["detalle"] and p["detalle"] not in ("FT", "Final") else ""
            torneo = f" – {p['torneo']}" if p["torneo"] != CONFIG["liga_principal"]["nombre"] else ""
            lineas.append(estrella(p) + marcador(p) + extra + torneo)
        bloques.append("🏁 *Final*\n" + "\n".join(lineas))
    if bloques:
        if not enviar("\n\n".join(bloques)) and not SIMULAR:
            # si falló el envío, se reintenta en la próxima vuelta
            for p in entretiempos:
                estado["partidos"][p["id"]].pop("et", None)
            for p in finales:
                estado["partidos"][p["id"]].pop("fin", None)
    return queda_algo


def modo_envivo(estado, minutos):
    fin = time.time() + minutos * 60
    while True:
        queda_algo = vigilar_una_vez(estado)
        guardar_estado(estado)
        if not queda_algo:
            print("No hay partidos en juego ni por empezar en la próxima hora. Fin.")
            return
        if time.time() + CONFIG["segundos_entre_consultas"] > fin:
            return
        time.sleep(CONFIG["segundos_entre_consultas"])


def main():
    global SIMULAR
    modo = sys.argv[1] if len(sys.argv) > 1 else "auto"
    estado = cargar_estado()

    if modo == "ver":
        SIMULAR = True
        enviar(texto_resumen(ahora().date()) or "(hoy no habría resumen)")
        print("\nAvisos en vivo que se mandarían ahora (entretiempo/final):")
        vigilar_una_vez({"partidos": {}})
        return
    if modo == "grupos":
        # Lista los grupos del WhatsApp vinculado para saber el CHAT_ID
        for c in green_api("getContacts") or []:
            if c.get("type") == "group" or str(c.get("id", "")).endswith("@g.us"):
                print(f"{c.get('name')}  ->  {c.get('id')}")
        return
    if modo == "prueba":
        enviar(sys.argv[2] if len(sys.argv) > 2 else "✅ Prueba del bot de fútbol")
        return
    if modo == "resumen":
        modo_resumen(estado, forzar=True)
    elif modo == "envivo":
        modo_envivo(estado, CONFIG["minutos_por_ejecucion"])
    elif modo == "auto":
        if ahora().hour >= CONFIG["hora_resumen_diario"]:
            modo_resumen(estado)
            guardar_estado(estado)
        modo_envivo(estado, CONFIG["minutos_por_ejecucion"])
    else:
        print(__doc__)
        return
    guardar_estado(estado)


if __name__ == "__main__":
    main()
