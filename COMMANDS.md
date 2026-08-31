# Basic Django commands

Run these from the project root:

`d:\dec-plus-tech-catalog`

---

## Windows (local)

**Start the server**

```powershell
python manage.py runserver
```

Open http://127.0.0.1:8000/

To listen on all network interfaces (LAN access):

```powershell
python manage.py runserver 0.0.0.0:8000
```

**Create migration files** (after model changes)

```powershell
python manage.py makemigrations
```

**Apply migrations** to the database

```powershell
python manage.py migrate
```

Typical order after a model change:

```powershell
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
```

---

## Remote server

The project lives in **Desktop/parts_find**. Open a terminal, then:

**1. Go to the project folder**

```bash
cd ~/Desktop/parts_find
```

**2. Activate the virtual environment**

```bash
source venv/bin/activate
```

If that fails, try:

```bash
source .venv/bin/activate
```

Your prompt should show `(venv)` or `(.venv)` when it is active.

**3. Start the server**

```bash
python3 manage.py runserver 192.168.0.150:8000
```

Then open http://192.168.0.150:8000/ on a browser.

Full sequence:

```bash
cd ~/Desktop/parts_find
source venv/bin/activate
python3 manage.py runserver 192.168.0.150:8000
```

**Create migration files** (with the environment still active)

```bash
python3 manage.py makemigrations
```

**Apply migrations**

```bash
python3 manage.py migrate
```

**Stop the server:** press `Ctrl+C` in that terminal.

**Leave the environment** (optional, when you are done):

```bash
deactivate
```

