from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import sqlite3
import json
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "barbearia.db"

def init_db():
    if not os.path.exists(DB_NAME):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE barbeiros (
                id INTEGER PRIMARY KEY,
                nome TEXT,
                experiencia INTEGER,
                especialidade TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE servicos (
                id INTEGER PRIMARY KEY,
                nome TEXT,
                preco REAL,
                duracao_min INTEGER
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE agendamentos (
                id INTEGER PRIMARY KEY,
                barbeiro_id INTEGER,
                servico_id INTEGER,
                cliente_nome TEXT,
                cliente_telefone TEXT,
                data_hora TEXT,
                status TEXT DEFAULT 'pendente',
                FOREIGN KEY (barbeiro_id) REFERENCES barbeiros(id),
                FOREIGN KEY (servico_id) REFERENCES servicos(id)
            )
        ''')
        
        cursor.execute("INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (1, 'Joao', 15, 'degrade')")
        cursor.execute("INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (2, 'Pedro', 8, 'barba e bigode')")
        cursor.execute("INSERT INTO barbeiros (id, nome, experiencia, especialidade) VALUES (3, 'Carlos', 5, 'cortes modernos jovens')")
        
        cursor.execute("INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (1, 'Corte Masculino', 45.0, 30)")
        cursor.execute("INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (2, 'Barba Completa', 35.0, 30)")
        cursor.execute("INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (3, 'Combo Cabelo+Barba', 75.0, 60)")
        cursor.execute("INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (4, 'Pigmentacao', 90.0, 90)")
        cursor.execute("INSERT INTO servicos (id, nome, preco, duracao_min) VALUES (5, 'Sobrancelha Masculina', 20.0, 15)")
        
        conn.commit()
        conn.close()

init_db()

@app.get("/")
async def serve_frontend():
    return FileResponse("frontend.html", media_type="text/html")

@app.get("/api/barbeiros")
def list_barbeiros():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM barbeiros")
    barbeiros = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return barbeiros

@app.get("/api/servicos")
def list_servicos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM servicos")
    servicos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return servicos

@app.post("/api/agendamentos")
def create_agendamento(data: dict):
    barbeiro_id = data.get("barbeiro_id")
    servico_id = data.get("servico_id")
    cliente_nome = data.get("cliente_nome")
    cliente_telefone = data.get("cliente_telefone")
    data_hora = data.get("data_hora")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO agendamentos (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora, status) VALUES (?, ?, ?, ?, ?, 'pendente')",
        (barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora)
    )
    conn.commit()
    agendamento_id = cursor.lastrowid
    conn.close()
    
    print(f"[WHATSAPP MOCK] Olá {cliente_nome}, agendamento confirmado pra {data_hora}")
    
    return {"id": agendamento_id, "status": "pendente"}

@app.delete("/api/agendamentos/{agendamento_id}")
def cancel_agendamento(agendamento_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT data_hora FROM agendamentos WHERE id = ?", (agendamento_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="Agendamento nao encontrado")
    
    data_hora_str = result[0]
    data_hora = datetime.fromisoformat(data_hora_str)
    agora = datetime.now()
    diferenca = (data_hora - agora).total_seconds() / 3600
    
    if diferenca < 1:
        conn.close()
        raise HTTPException(status_code=403, detail="Cancelamento nao permitido menos de 1h antes")
    
    cursor.execute("DELETE FROM agendamentos WHERE id = ?", (agendamento_id,))
    conn.commit()
    conn.close()
    
    return {"status": "cancelado"}

@app.get("/api/agendamentos")
def list_agendamentos_by_phone(telefone: str):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agendamentos WHERE cliente_telefone = ? AND status = 'pendente'", (telefone,))
    agendamentos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return agendamentos

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

```