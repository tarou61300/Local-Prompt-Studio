Local Prompt Studio portable data folder

The application stores its settings, logs, optional history, downloaded MiniMax
H3 Prompt Skill, and llama-server logs in this folder.

Prompt Library data is stored independently from History. The legacy Default
Dataset remains in prompt_library.sqlite3 and is created only after Prompt
Library is opened for the first time. Additional independent Datasets and their
small registry are stored under prompt_library_datasets/ and in
prompt_library_datasets.json. Source runs use
<repository>/.dev-data/prompt_library.sqlite3 and the corresponding managed
Dataset paths instead.

prompt_library.sqlite3, prompt_library.sqlite3-wal,
prompt_library.sqlite3-shm, prompt_library_datasets.json, and the
prompt_library_datasets/ directory are user-generated data and are never
bundled in the official portable ZIP.

If ComfyUI pairing is used, comfyui_credentials.dat is also stored here. Its
credential is protected with Windows DPAPI CurrentUser and is not stored in
config.json. Never publish or share that file.

No Windows registry settings, services, Start Menu shortcuts, or desktop
shortcuts are created. To remove Local Prompt Studio completely, close the app
and delete the extracted portable folder.
