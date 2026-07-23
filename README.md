# Lucas Oura Export

Proyecto personal para autorizar una cuenta Oura mediante OAuth 2.0 y descargar
los datos disponibles en la API V2 como archivos JSON.

El proyecto:

- no incluye ni publica credenciales;
- recibe el callback OAuth solamente en `localhost`;
- guarda los tokens y las exportaciones únicamente en la computadora;
- renueva automáticamente el access token usando el refresh token;
- descarga cada tipo de dato en un JSON independiente.

## 1. Publicar las páginas requeridas por Oura

1. Creá un repositorio público de GitHub llamado `oura-personal`.
2. Subí el contenido de esta carpeta. No subas nunca `.env`,
   `.oura_tokens.json` ni la carpeta `exports`.
3. Activá GitHub Pages para la carpeta `/docs` de la rama principal.
4. Reemplazá `TU_USUARIO` por tu nombre de usuario de GitHub en las siguientes
   direcciones.

Completá el formulario de Oura así:

| Campo | Valor |
| --- | --- |
| Display Name | `Lucas Oura Export` |
| Description | `Personal application used by the account owner to export and analyze his own Oura data locally.` |
| Website | `https://TU_USUARIO.github.io/oura-personal/` |
| Privacy Policy | `https://TU_USUARIO.github.io/oura-personal/privacy.html` |
| Terms of Service | `https://TU_USUARIO.github.io/oura-personal/terms.html` |
| Redirect URI | `http://localhost:8000/callback` |

Para exportar todos los datos, seleccioná estos scopes:

- Personal
- Daily
- Heartrate
- Tag
- Workout
- Session
- SpO2
- Stress
- Heart Health
- Ring Configuration

`Email` es opcional y este proyecto no lo necesita.

La redirect URI tiene que coincidir exactamente, incluyendo `http`, puerto,
ruta y ausencia de una barra final.

## 2. Configurar Python en Windows

Abrí PowerShell dentro de la carpeta del proyecto y ejecutá:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
notepad .env
```

No requiere instalar paquetes externos.

Dentro de `.env`, pegá el Client ID y Client Secret entregados por Oura:

```dotenv
OURA_CLIENT_ID=tu_client_id
OURA_CLIENT_SECRET=tu_client_secret
OURA_REDIRECT_URI=http://localhost:8000/callback
```

No publiques `.env`, el Client Secret ni el archivo de tokens.

## 3. Autorizar la cuenta

Con el entorno virtual activado:

```powershell
python oura_export.py auth
```

El script abrirá el navegador. Iniciá sesión en Oura, aceptá los permisos y
esperá el mensaje de confirmación. Los tokens quedarán guardados localmente en
`.oura_tokens.json`.

## 4. Descargar datos

Últimos 30 días:

```powershell
python oura_export.py download --days 30
```

Un período determinado:

```powershell
python oura_export.py download --start 2026-01-01 --end 2026-07-23
```

Solo algunos conjuntos:

```powershell
python oura_export.py download --days 30 --only daily_sleep sleep heartrate
```

Los resultados se guardan en una subcarpeta de `exports`. Cada endpoint produce
su propio JSON y `_summary.json` indica cuáles se descargaron o fueron omitidos.
Un `403` suele indicar que ese scope no fue autorizado; un conjunto vacío puede
significar que el anillo todavía no produjo esa métrica.

## Tipos de datos incluidos

El descargador contempla:

- actividad, readiness y sueño diarios;
- períodos de sueño y recomendación de horario;
- frecuencia cardíaca;
- entrenamientos y sesiones;
- tags tradicionales y mejorados;
- SpO2, estrés y resiliencia;
- edad cardiovascular y VO2 max;
- períodos de descanso;
- configuración y batería del anillo;
- información personal.

La API obtiene información de Oura Cloud. Antes de descargar, abrí la
aplicación Oura y verificá que el anillo se haya sincronizado.

## Seguridad

- `.gitignore` excluye credenciales, tokens y exportaciones.
- El refresh token de Oura es de un solo uso. El script guarda inmediatamente
  el nuevo refresh token después de cada renovación.
- Para revocar el acceso, eliminá la integración desde tu cuenta Oura y borrá
  `.oura_tokens.json`.
- Las métricas de Oura no sustituyen una evaluación médica.

## Prueba local

```powershell
python -m unittest discover -s tests
```
