from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "barbearia.db"

def init_db():
    """Inicializa DB e seed"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Criar tabelas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS barbeiros (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            experiencia INTEGER NOT NULL,
            especialidade TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            preco REAL NOT NULL,
            duracao_min INTEGER NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agendamentos (
            id INTEGER PRIMARY KEY,
            barbeiro_id INTEGER NOT NULL,
            servico_id INTEGER NOT NULL,
            cliente_nome TEXT NOT NULL,
            cliente_telefone TEXT NOT NULL,
            data_hora TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'pendente',
            FOREIGN KEY(barbeiro_id) REFERENCES barbeiros(id),
            FOREIGN KEY(servico_id) REFERENCES servicos(id)
        )
    """)
    
    conn.commit()
    
    # Verificar se já tem dados
    cursor.execute("SELECT COUNT(*) FROM barbeiros")
    if cursor.fetchone()[0] == 0:
        # Seed barbeiros
        barbeiros_data = [
            (1, 'Joao', 15, 'degrade'),
            (2, 'Pedro', 8, 'barba e bigode'),
            (3, 'Carlos', 5, 'cortes jovens')
        ]
        cursor.executemany(
            "INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (?, ?, ?, ?)",
            barbeiros_data
        )
        
        # Seed servicos
        servicos_data = [
            (1, 'Corte Masculino', 45.0, 30),
            (2, 'Barba Completa', 35.0, 30),
            (3, 'Combo Cabelo + Barba', 75.0, 60),
            (4, 'Pigmentacao de Cabelo', 90.0, 90),
            (5, 'Sobrancelha Masculina', 20.0, 15)
        ]
        cursor.executemany(
            "INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (?, ?, ?, ?)",
            servicos_data
        )
        
        conn.commit()
        print("[BOOT] DB inicializado com seed")
    
    conn.close()

init_db()

@app.get("/")
async def serve_frontend():
    return FileResponse("frontend.html")

@app.get("/api/barbeiros")
async def list_barbeiros():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM barbeiros ORDER BY id")
    barbeiros = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return barbeiros

@app.get("/api/servicos")
async def list_servicos():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM servicos ORDER BY id")
    servicos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return servicos

@app.post("/api/agendamentos")
async def create_agendamento(data: dict):
    barbeiro_id = data.get("barbeiro_id")
    servico_id = data.get("servico_id")
    cliente_nome = data.get("cliente_nome")
    cliente_telefone = data.get("cliente_telefone")
    data_hora = data.get("data_hora")
    
    if not all([barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora]):
        raise HTTPException(status_code=400, detail="Dados incompletos")
    
    try:
        datetime.fromisoformat(data_hora)
    except:
        raise HTTPException(status_code=400, detail="data_hora invalida")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM barbeiros WHERE id = ?", (barbeiro_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Barbeiro nao encontrado")
    
    cursor.execute("SELECT id FROM servicos WHERE id = ?", (servico_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Servico nao encontrado")
    
    cursor.execute(
        """INSERT INTO agendamentos 
           (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status)
           VALUES (?, ?, ?, ?, ?, 'pendente')""",
        (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora)
    )
    
    agendamento_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Mock WhatsApp log
    print(f"[WhatsApp] Olá {cliente_nome}, agendamento confirmado pra {data_hora}")
    
    return {"id": agendamento_id, "status": "pendente"}

@app.delete("/api/agendamentos/{agendamento_id}")
async def cancel_agendamento(agendamento_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT data_hora FROM agendamentos WHERE id = ?", (agendamento_id,))
    row = cursor.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Agendamento nao encontrado")
    
    data_hora = datetime.fromisoformat(row[0])
    agora = datetime.now()
    diff_minutes = (data_hora - agora).total_seconds() / 60
    
    if diff_minutes < 60:
        raise HTTPException(status_code=403, detail="Cancelamento nao permitido (menos de 1 hora)")
    
    cursor.execute("UPDATE agendamentos SET status = 'cancelado' WHERE id = ?", (agendamento_id,))
    conn.commit()
    conn.close()
    
    return {"status": "cancelado"}

@app.get("/api/agendamentos")
async def list_agendamentos(telefone: str = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if telefone:
        cursor.execute(
            "SELECT * FROM agendamentos WHERE cliente_telefone = ? AND status = 'pendente' ORDER BY data_hora",
            (telefone,)
        )
    else:
        cursor.execute("SELECT * FROM agendamentos WHERE status = 'pendente' ORDER BY data_hora")
    
    agendamentos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return agendamentos

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
```