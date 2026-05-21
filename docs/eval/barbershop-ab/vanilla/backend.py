from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from datetime import datetime, timedelta
import sqlite3
import json
import logging

# Logging config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = "sqlite:///./barbershop.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Barbeiro(Base):
    __tablename__ = "barbeiros"
    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, index=True)
    telefone = Column(String)

class Servico(Base):
    __tablename__ = "servicos"
    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, index=True)
    preco = Column(Float)
    duracao_minutos = Column(Integer)

class Agendamento(Base):
    __tablename__ = "agendamentos"
    id = Column(Integer, primary_key=True)
    cliente_nome = Column(String)
    cliente_telefone = Column(String)
    barbeiro_id = Column(Integer)
    servico_id = Column(Integer)
    data_hora = Column(DateTime)
    confirmado = Column(Boolean, default=False)
    criado_em = Column(DateTime, default=datetime.utcnow)

# Schemas
class BarbeiroSchema(BaseModel):
    id: int
    nome: str
    telefone: str

    class Config:
        from_attributes = True

class ServicoSchema(BaseModel):
    id: int
    nome: str
    preco: float
    duracao_minutos: int

    class Config:
        from_attributes = True

class AgendamentoCreate(BaseModel):
    cliente_nome: str
    cliente_telefone: str
    barbeiro_id: int
    servico_id: int
    data_hora: datetime

class AgendamentoSchema(BaseModel):
    id: int
    cliente_nome: str
    cliente_telefone: str
    barbeiro_id: int
    servico_id: int
    data_hora: datetime
    confirmado: bool
    criado_em: datetime

    class Config:
        from_attributes = True

class ConfirmacaoRequest(BaseModel):
    telefone: str

# FastAPI app
app = FastAPI(title="BarberShop API", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Init DB
def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Seed barbeiros
    if db.query(Barbeiro).count() == 0:
        barbeiros = [
            Barbeiro(nome="Carlos Silva", telefone="11987654321"),
            Barbeiro(nome="João Santos", telefone="11987654322"),
            Barbeiro(nome="Ricardo Oliveira", telefone="11987654323"),
        ]
        db.add_all(barbeiros)
        db.commit()
    
    # Seed servicos
    if db.query(Servico).count() == 0:
        servicos = [
            Servico(nome="Corte Simples", preco=40.00, duracao_minutos=30),
            Servico(nome="Corte com Barba", preco=60.00, duracao_minutos=45),
            Servico(nome="Barba Completa", preco=35.00, duracao_minutos=30),
            Servico(nome="Hidratação Capilar", preco=50.00, duracao_minutos=40),
            Servico(nome="Pigmentação", preco=70.00, duracao_minutos=50),
        ]
        db.add_all(servicos)
        db.commit()
    
    db.close()

init_db()

# Routes
@app.get("/")
async def root():
    return FileResponse("frontend.html")

@app.get("/api/v1/barbeiros", response_model=list[BarbeiroSchema])
async def get_barbeiros(db: Session = Depends(get_db)):
    return db.query(Barbeiro).all()

@app.get("/api/v1/servicos", response_model=list[ServicoSchema])
async def get_servicos(db: Session = Depends(get_db)):
    return db.query(Servico).all()

@app.post("/api/v1/agendamentos", response_model=AgendamentoSchema)
async def criar_agendamento(agendamento: AgendamentoCreate, db: Session = Depends(get_db)):
    # Validações
    if not db.query(Barbeiro).filter(Barbeiro.id == agendamento.barbeiro_id).first():
        raise HTTPException(status_code=404, detail="Barbeiro não encontrado")
    
    if not db.query(Servico).filter(Servico.id == agendamento.servico_id).first():
        raise HTTPException(status_code=404, detail="Serviço não encontrado")
    
    if agendamento.data_hora < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Data/hora não pode ser no passado")
    
    # Conflito de horário
    conflito = db.query(Agendamento).filter(
        Agendamento.barbeiro_id == agendamento.barbeiro_id,
        Agendamento.data_hora == agendamento.data_hora,
        Agendamento.confirmado == True
    ).first()
    
    if conflito:
        raise HTTPException(status_code=409, detail="Horário indisponível")
    
    novo_agendamento = Agendamento(
        cliente_nome=agendamento.cliente_nome,
        cliente_telefone=agendamento.cliente_telefone,
        barbeiro_id=agendamento.barbeiro_id,
        servico_id=agendamento.servico_id,
        data_hora=agendamento.data_hora,
        confirmado=False
    )
    
    db.add(novo_agendamento)
    db.commit()
    db.refresh(novo_agendamento)
    
    return novo_agendamento

@app.post("/api/v1/agendamentos/{id}/confirmar")
async def confirmar_agendamento(id: int, confirmacao: ConfirmacaoRequest, db: Session = Depends(get_db)):
    agendamento = db.query(Agendamento).filter(Agendamento.id == id).first()
    
    if not agendamento:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    if agendamento.cliente_telefone != confirmacao.telefone:
        raise HTTPException(status_code=403, detail="Telefone não coincide")
    
    agendamento.confirmado = True
    db.commit()
    
    # Simular envio WhatsApp
    servico = db.query(Servico).filter(Servico.id == agendamento.servico_id).first()
    barbeiro = db.query(Barbeiro).filter(Barbeiro.id == agendamento.barbeiro_id).first()
    
    mensagem = f"""
    [WhatsApp Simulado]
    Olá {agendamento.cliente_nome}!
    
    Seu agendamento foi confirmado:
    📅 Data/Hora: {agendamento.data_hora.strftime('%d/%m/%Y %H:%M')}
    💈 Serviço: {servico.nome}
    💰 Valor: R$ {servico.preco:.2f}
    👨 Barbeiro: {barbeiro.nome}
    
    Endereço: Rua das Flores, 123 - Centro, São Paulo, SP
    📞 Telefone: (11) 3333-4444
    
    Até logo!
    """
    
    logger.info(mensagem)
    
    return {"status": "confirmado", "id": id}

@app.get("/api/v1/agendamentos")
async def listar_agendamentos(telefone: str, db: Session = Depends(get_db)):
    agendamentos = db.query(Agendamento).filter(
        Agendamento.cliente_telefone == telefone
    ).all()
    
    resultado = []
    for agendamento in agendamentos:
        servico = db.query(Servico).filter(Servico.id == agendamento.servico_id).first()
        barbeiro = db.query(Barbeiro).filter(Barbeiro.id == agendamento.barbeiro_id).first()
        
        resultado.append({
            "id": agendamento.id,
            "cliente_nome": agendamento.cliente_nome,
            "cliente_telefone": agendamento.cliente_telefone,
            "barbeiro": barbeiro.nome if barbeiro else "",
            "servico": servico.nome if servico else "",
            "preco": servico.preco if servico else 0,
            "data_hora": agendamento.data_hora.isoformat(),
            "confirmado": agendamento.confirmado
        })
    
    return resultado

@app.get("/api/v1/disponibilidade")
async def get_disponibilidade(barbeiro_id: int, data: str, db: Session = Depends(get_db)):
    from datetime import time
    
    horarios_disponiveis = []
    horarios_sistema = [
        "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
        "12:00", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"
    ]
    
    for horario in horarios_sistema:
        data_hora = datetime.strptime(f"{data} {horario}", "%Y-%m-%d %H:%M")
        
        conflito = db.query(Agendamento).filter(
            Agendamento.barbeiro_id == barbeiro_id,
            Agendamento.data_hora == data_hora,
            Agendamento.confirmado == True
        ).first()
        
        if not conflito and data_hora > datetime.utcnow():
            horarios_disponiveis.append(horario)
    
    return {"horarios": horarios_disponiveis}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)