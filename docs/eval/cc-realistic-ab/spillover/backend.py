from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "barbearia.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS barbeiros (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        experiencia INTEGER NOT NULL,
        especialidade TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS servicos (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        preco REAL NOT NULL,
        duracao_min INTEGER NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS agendamentos (
        id INTEGER PRIMARY KEY,
        barbeiro_id INTEGER NOT NULL,
        servico_id INTEGER NOT NULL,
        cliente_nome TEXT NOT NULL,
        cliente_telefone TEXT NOT NULL,
        data_hora TIMESTAMP NOT NULL,
        status TEXT DEFAULT 'pendente',
        FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id),
        FOREIGN KEY (servico_id) REFERENCES servicos(id)
    );
    """)
    
    cursor.execute("SELECT COUNT(*) FROM barbeiros")
    if cursor.fetchone()[0] == 0:
        barbeiros = [
            (1, 'Joao', 15, 'degrade'),
            (2, 'Pedro', 8, 'barba e bigode'),
            (3, 'Carlos', 5, 'cortes jovens')
        ]
        cursor.executemany("INSERT INTO barbeiros VALUES (?, ?, ?, ?)", barbeiros)
    
    cursor.execute("SELECT COUNT(*) FROM servicos")
    if cursor.fetchone()[0] == 0:
        servicos = [
            (1, 'Corte Masculino', 45.00, 30),
            (2, 'Barba Completa', 35.00, 30),
            (3, 'Combo Cabelo + Barba', 75.00, 60),
            (4, 'Pigmentacao de Cabelo', 90.00, 90),
            (5, 'Sobrancelha Masculina', 20.00, 15)
        ]
        cursor.executemany("INSERT INTO servicos VALUES (?, ?, ?, ?)", servicos)
    
    conn.commit()
    conn.close()

init_db()

@app.get("/")
async def serve_frontend():
    return FileResponse("frontend.html", media_type="text/html")

@app.get("/api/barbeiros")
def get_barbeiros():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM barbeiros ORDER BY id")
    barbeiros = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return barbeiros

@app.get("/api/servicos")
def get_servicos():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM servicos ORDER BY id")
    servicos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return servicos

@app.post("/api/agendamentos")
def create_agendamento(barbeiro_id: int, servico_id: int, cliente_nome: str, cliente_telefone: str, data_hora: str):
    try:
        agendamento_dt = datetime.fromisoformat(data_hora)
    except:
        raise HTTPException(status_code=400, detail="data_hora inválida")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM barbeiros WHERE id = ?", (barbeiro_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Barbeiro não encontrado")
    
    cursor.execute("SELECT * FROM servicos WHERE id = ?", (servico_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Serviço não encontrado")
    
    cursor.execute(
        "INSERT INTO agendamentos (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status) VALUES (?, ?, ?, ?, ?, ?)",
        (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, 'pendente')
    )
    conn.commit()
    agendamento_id = cursor.lastrowid
    
    print(f"[WHATSAPP] Olá {cliente_nome}, agendamento confirmado pra {data_hora}")
    
    conn.close()
    return {"id": agendamento_id, "status": "confirmado"}

@app.delete("/api/agendamentos/{agendamento_id}")
def cancel_agendamento(agendamento_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT data_hora FROM agendamentos WHERE id = ?", (agendamento_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    agendamento_dt = datetime.fromisoformat(row[0])
    agora = datetime.now()
    diferenca = agendamento_dt - agora
    
    if diferenca < timedelta(hours=1):
        conn.close()
        raise HTTPException(status_code=403, detail="Cancelamento apenas com 1h de antecedência")
    
    cursor.execute("DELETE FROM agendamentos WHERE id = ?", (agendamento_id,))
    conn.commit()
    conn.close()
    
    return {"status": "cancelado"}

@app.get("/api/agendamentos")
def get_agendamentos(telefone: Optional[str] = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if telefone:
        cursor.execute("SELECT * FROM agendamentos WHERE cliente_telefone = ? AND status = 'pendente' ORDER BY data_hora", (telefone,))
    else:
        cursor.execute("SELECT * FROM agendamentos WHERE status = 'pendente' ORDER BY data_hora")
    
    agendamentos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return agendamentos
```
