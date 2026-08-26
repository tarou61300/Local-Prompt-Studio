Local Prompt Studio portable data folder

The application stores its settings, logs, optional history, downloaded MiniMax
H3 Prompt Skill, and llama-server logs in this folder.

Prompt Library data is stored independently from History in
prompt_library.sqlite3. The database is created only after Prompt Library is
opened for the first time. Source runs use
<repository>/.dev-data/prompt_library.sqlite3 instead.

prompt_library.sqlite3, prompt_library.sqlite3-wal, and
prompt_library.sqlite3-shm are user-generated data and are never bundled in the
official portable ZIP.

If ComfyUI pairing is used, comfyui_credentials.dat is also stored here. Its
credential is protected with Windows DPAPI CurrentUser and is not stored in
config.json. Never publish or share that file.

No Windows registry settings, services, Start Menu shortcuts, or desktop
shortcuts are created. To remove Local Prompt Studio completely, close the app
and delete the extracted portable folder.
