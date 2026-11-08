# Bot de fútbol por WhatsApp

Avisos por WhatsApp (vía CallMeBot) sobre la Primera División de Chile:

- **Cada mañana (9:00):** partidos de Primera de hoy; partidos de mañana y resultados de ayer de los equipos favoritos (⭐).
- **En vivo:** resultado al entretiempo y al final de cada partido del día.

## Ajustes (`config.json`)
- `favoritos`: nombres de equipos tal como los escribe ESPN (`"Universidad de Chile"`, `"Colo Colo"`).
- `avisar_entretiempo_y_final`: `"todos"` o `"favoritos"`.
- `hora_resumen_diario`: hora (Chile) desde la que se manda el resumen.

## Partidos que ESPN no tiene (Fernández Vial, Tercera B)
Se anotan en `partidos_manuales.csv`:

```
fecha,hora,local,visita,torneo,resultado
2026-11-15,16:00,Fernández Vial,Deportes Rengo,Tercera B,
```
Con eso llega el aviso el día antes, el día del partido y, si se completa `resultado` (ej. `2-1`), el resultado al día siguiente.

## Envío (Green-API)
Secretos de GitHub: `GREEN_API_URL`, `GREEN_ID`, `GREEN_TOKEN` (datos de la instancia) y
`CHAT_ID` (el grupo de WhatsApp, termina en `@g.us`; `python bot.py grupos` lo muestra).

## Probar en el PC (PowerShell)
```
$env:GREEN_API_URL="https://XXXX.api.greenapi.com"; $env:GREEN_ID="..."; $env:GREEN_TOKEN="..."
python bot.py grupos     (muestra los grupos y su CHAT_ID)
$env:CHAT_ID="...@g.us"
python bot.py ver        (muestra el resumen sin enviar)
python bot.py prueba     (manda un mensaje de prueba)
```
