"""
Modelos SQLAlchemy — sanmarcosguayaquil

Tabla paciente: nombre en SINGULAR, columnas en MAYÚSCULAS
  PK: ID_PACIENTE
  JOIN: paciente.ID_PACIENTE = consultas.idpacientes
"""
from sqlalchemy import Column, DateTime, Date, Integer, String, Text, ForeignKey, func
from sqlalchemy.orm import relationship

from app.db.session import Base


class Paciente(Base):
    """
    Tabla: paciente (singular)
    Columnas en mayúsculas según el esquema real de sanmarcosguayaquil.
    """
    __tablename__ = "paciente"

    ID_PACIENTE    = Column(Integer, primary_key=True, index=True)
    NOMBRES        = Column(String(255), nullable=True)
    APELLIDOS      = Column(String(255), nullable=True)
    IDENTIFICACION = Column(String(255), nullable=True)
    SEXO           = Column(String(1),   nullable=True)
    TELEFONO       = Column(String(15),  nullable=True)
    CELULAR        = Column(String(255), nullable=True)
    EMAIL          = Column(String(255), nullable=True)
    FECHA_NAC      = Column(Date,        nullable=True)
    FECHA_INGRESO  = Column(DateTime,    nullable=True)
    numero_historia_clinica = Column(String(255), nullable=True)
    ocupacion      = Column(String(255), nullable=True)
    lugar_trabajo  = Column(String(255), nullable=True)

    consultas = relationship(
        "Consulta",
        back_populates="paciente",
        primaryjoin="Paciente.ID_PACIENTE == Consulta.idpacientes",
        foreign_keys="[Consulta.idpacientes]",
    )

    @property
    def nombre_completo(self) -> str:
        return f"{self.NOMBRES or ''} {self.APELLIDOS or ''}".strip()


class Consulta(Base):
    """Tabla principal de consultas médicas."""
    __tablename__ = "consultas"

    idConsultas    = Column(Integer, primary_key=True, index=True)
    idpacientes    = Column(Integer, ForeignKey("paciente.ID_PACIENTE"), nullable=True, index=True)
    fecha          = Column(DateTime, nullable=True)
    idtrabajadores = Column(Integer, nullable=True)

    # Columnas RAG principales
    motivoConsulta  = Column(Text, nullable=True)
    enfermedadActual = Column(Text, nullable=True)
    examenFisico    = Column(Text, nullable=True)

    # Columnas de contexto adicional
    observaciones  = Column(Text,       nullable=True)
    tipo_consulta  = Column(String(80), nullable=True)
    nombre_empleado = Column(String(200), nullable=True)
    area_trabajo   = Column(String(150), nullable=True)
    lugar_trabajo  = Column(String(150), nullable=True)
    estado_id      = Column(Integer,    nullable=True)

    paciente = relationship(
        "Paciente",
        back_populates="consultas",
        primaryjoin="Consulta.idpacientes == Paciente.ID_PACIENTE",
        foreign_keys="[Consulta.idpacientes]",
    )

    def to_rag_text(self) -> str:
        nombre = self.paciente.nombre_completo if self.paciente else ""
        parts = []
        if nombre:
            parts.append(f"Paciente: {nombre}.")
        if self.fecha:
            parts.append(f"Fecha: {self.fecha}.")
        if self.motivoConsulta:
            parts.append(f"Motivo de consulta: {self.motivoConsulta}.")
        if self.enfermedadActual:
            parts.append(f"Enfermedad actual: {self.enfermedadActual}.")
        if self.examenFisico:
            parts.append(f"Examen físico: {self.examenFisico}.")
        if self.observaciones:
            parts.append(f"Observaciones: {self.observaciones}.")
        return " ".join(parts)

    def to_dict(self) -> dict:
        nombre = self.paciente.nombre_completo if self.paciente else None
        return {
            "idConsultas":     self.idConsultas,
            "idpacientes":     self.idpacientes,
            "nombre_paciente": nombre,
            "fecha":           str(self.fecha) if self.fecha else None,
            "motivoConsulta":  self.motivoConsulta,
            "enfermedadActual": self.enfermedadActual,
            "examenFisico":    self.examenFisico,
            "observaciones":   self.observaciones,
            "tipo_consulta":   self.tipo_consulta,
            "nombre_empleado": self.nombre_empleado,
        }


class ConversationLog(Base):
    """Log de respuestas finales del Agente 1."""
    __tablename__ = "conversation_logs"

    id            = Column(Integer, primary_key=True, index=True)
    user_message  = Column(Text, nullable=False)
    agent_response = Column(Text, nullable=False)
    llm_provider  = Column(String(30), nullable=True)
    created_at    = Column(DateTime, server_default=func.now())


class ConversationSession(Base):
    """Historial de turnos por sesión — persistido por Agente 3."""
    __tablename__ = "conversation_sessions"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), nullable=False, index=True)
    role       = Column(String(20), nullable=False)
    content    = Column(Text, nullable=False)
    intent     = Column(String(80), nullable=True)
    source     = Column(String(20), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
