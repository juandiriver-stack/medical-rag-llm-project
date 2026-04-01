"""
Herramienta MySQL — salidas JSON estandarizadas.
Todos los métodos retornan dicts con claves consistentes,
sin valores nulos innecesarios (se omiten campos None).
"""
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.orm import Session


def _clean(d: dict) -> dict:
    """Elimina claves cuyo valor es None para reducir tokens."""
    return {k: v for k, v in d.items() if v is not None}


def _tabla_existe(db: Session, tabla: str) -> bool:
    try:
        db.execute(text(f"SELECT 1 FROM `{tabla}` LIMIT 1"))
        return True
    except Exception:
        return False


class ConsultasTool:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._tiene_paciente = _tabla_existe(db, "paciente")

    # ── SELECT base ─────────────────────────────────────────────────────
    def _select_consultas(self) -> str:
        if self._tiene_paciente:
            return """
                SELECT
                    c.idConsultas,
                    c.idpacientes,
                    c.fecha,
                    c.motivoConsulta,
                    c.enfermedadActual,
                    c.examenFisico,
                    c.observaciones,
                    c.tipo_consulta,
                    c.nombre_empleado,
                    CONCAT_WS(' ', p.NOMBRES, p.APELLIDOS) AS nombre_paciente,
                    p.IDENTIFICACION   AS cedula_paciente,
                    p.TELEFONO         AS telefono_paciente,
                    p.CELULAR          AS celular_paciente,
                    p.EMAIL            AS email_paciente,
                    p.SEXO             AS sexo_paciente,
                    p.numero_historia_clinica,
                    p.ocupacion        AS ocupacion_paciente,
                    p.lugar_trabajo    AS lugar_trabajo_paciente
                FROM consultas c
                LEFT JOIN paciente p ON p.ID_PACIENTE = c.idpacientes
            """
        return """
            SELECT
                c.idConsultas, c.idpacientes, c.fecha,
                c.motivoConsulta, c.enfermedadActual, c.examenFisico,
                c.observaciones, c.tipo_consulta, c.nombre_empleado,
                NULL AS nombre_paciente,    NULL AS cedula_paciente,
                NULL AS telefono_paciente,  NULL AS celular_paciente,
                NULL AS email_paciente,     NULL AS sexo_paciente,
                NULL AS numero_historia_clinica,
                NULL AS ocupacion_paciente, NULL AS lugar_trabajo_paciente
            FROM consultas c
        """

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convierte fila SQL a dict JSON limpio (sin nulos)."""
        raw = {
            "id_consulta":             row.idConsultas,
            "id_paciente":             row.idpacientes,
            "nombre_paciente":         (row.nombre_paciente or "").strip() or None,
            "cedula":                  row.cedula_paciente,
            "telefono":                row.telefono_paciente or row.celular_paciente,
            "email":                   row.email_paciente,
            "sexo":                    row.sexo_paciente,
            "historia_clinica":        row.numero_historia_clinica,
            "ocupacion":               row.ocupacion_paciente,
            "lugar_trabajo":           row.lugar_trabajo_paciente,
            "fecha":                   str(row.fecha) if row.fecha else None,
            "motivo_consulta":         row.motivoConsulta,
            "enfermedad_actual":       row.enfermedadActual,
            "examen_fisico":           row.examenFisico,
            "observaciones":           row.observaciones,
            "tipo_consulta":           row.tipo_consulta,
            "medico":                  row.nombre_empleado,
        }
        return _clean(raw)

    # ── Consultas ────────────────────────────────────────────────────────
    def list_consultas(self, limit: int = 20) -> dict:
        sql = text(self._select_consultas() + " ORDER BY c.idConsultas DESC LIMIT :lim")
        rows = self.db.execute(sql, {"lim": limit}).fetchall()
        items = [self._row_to_dict(r) for r in rows]
        return {"type": "consultas_list", "count": len(items), "items": items}

    def get_consulta(self, consulta_id: int) -> dict:
        sql = text(self._select_consultas() + " WHERE c.idConsultas = :cid LIMIT 1")
        row = self.db.execute(sql, {"cid": consulta_id}).fetchone()
        if not row:
            return {"type": "error", "message": f"Consulta {consulta_id} no encontrada"}
        return {"type": "consulta_detail", "data": self._row_to_dict(row)}

    def search_consultas_by_motivo(self, keyword: str, limit: int = 10) -> dict:
        pattern = f"%{keyword}%"
        sql = text(self._select_consultas() + """
            WHERE c.motivoConsulta   LIKE :pat
               OR c.enfermedadActual LIKE :pat
               OR c.examenFisico     LIKE :pat
            LIMIT :lim
        """)
        rows = self.db.execute(sql, {"pat": pattern, "lim": limit}).fetchall()
        items = [self._row_to_dict(r) for r in rows]
        return {"type": "consultas_search", "keyword": keyword, "count": len(items), "items": items}

    def consultas_por_paciente(self, paciente_id: int) -> dict:
        sql = text(self._select_consultas() + """
            WHERE c.idpacientes = :pid ORDER BY c.fecha DESC LIMIT 100
        """)
        rows = self.db.execute(sql, {"pid": paciente_id}).fetchall()
        items = [self._row_to_dict(r) for r in rows]
        return {"type": "historial_paciente", "id_paciente": paciente_id,
                "count": len(items), "items": items}

    # ── Pacientes ─────────────────────────────────────────────────────────
    def list_pacientes(self, limit: int = 20) -> dict:
        if not self._tiene_paciente:
            sql = text("""SELECT DISTINCT idpacientes AS id FROM consultas
                          WHERE idpacientes IS NOT NULL ORDER BY idpacientes LIMIT :lim""")
            rows = self.db.execute(sql, {"lim": limit}).fetchall()
            return {"type": "pacientes_list", "count": len(rows),
                    "items": [{"id_paciente": r.id} for r in rows]}

        sql = text("""
            SELECT ID_PACIENTE AS id,
                   CONCAT_WS(' ', NOMBRES, APELLIDOS) AS nombre_completo,
                   IDENTIFICACION AS cedula, TELEFONO, CELULAR, EMAIL, SEXO,
                   FECHA_NAC AS fecha_nacimiento,
                   numero_historia_clinica, ocupacion, lugar_trabajo
            FROM paciente ORDER BY ID_PACIENTE ASC LIMIT :lim
        """)
        rows = self.db.execute(sql, {"lim": limit}).fetchall()
        items = [_clean({
            "id_paciente":      r.id,
            "nombre_completo":  (r.nombre_completo or "").strip() or None,
            "cedula":           r.cedula,
            "telefono":         r.TELEFONO or r.CELULAR,
            "email":            r.EMAIL,
            "sexo":             r.SEXO,
            "fecha_nacimiento": str(r.fecha_nacimiento) if r.fecha_nacimiento else None,
            "historia_clinica": r.numero_historia_clinica,
            "ocupacion":        r.ocupacion,
            "lugar_trabajo":    r.lugar_trabajo,
        }) for r in rows]
        return {"type": "pacientes_list", "count": len(items), "items": items}

    def get_paciente(self, paciente_id: int) -> dict:
        if not self._tiene_paciente:
            sql = text("SELECT COUNT(*) AS total FROM consultas WHERE idpacientes = :pid")
            row = self.db.execute(sql, {"pid": paciente_id}).fetchone()
            return {"type": "paciente_detail",
                    "data": {"id_paciente": paciente_id, "total_consultas": row.total if row else 0}}

        sql = text("""
            SELECT p.ID_PACIENTE AS id,
                   CONCAT_WS(' ', p.NOMBRES, p.APELLIDOS) AS nombre_completo,
                   p.IDENTIFICACION AS cedula, p.TELEFONO, p.CELULAR, p.EMAIL, p.SEXO,
                   p.FECHA_NAC AS fecha_nacimiento, p.numero_historia_clinica,
                   p.ocupacion, p.lugar_trabajo,
                   COUNT(c.idConsultas) AS total_consultas
            FROM paciente p
            LEFT JOIN consultas c ON c.idpacientes = p.ID_PACIENTE
            WHERE p.ID_PACIENTE = :pid GROUP BY p.ID_PACIENTE LIMIT 1
        """)
        row = self.db.execute(sql, {"pid": paciente_id}).fetchone()
        if not row:
            return {"type": "error", "message": f"Paciente {paciente_id} no encontrado"}
        return {"type": "paciente_detail", "data": _clean({
            "id_paciente":      row.id,
            "nombre_completo":  (row.nombre_completo or "").strip() or None,
            "cedula":           row.cedula,
            "telefono":         row.TELEFONO or row.CELULAR,
            "email":            row.EMAIL,
            "sexo":             row.SEXO,
            "fecha_nacimiento": str(row.fecha_nacimiento) if row.fecha_nacimiento else None,
            "historia_clinica": row.numero_historia_clinica,
            "ocupacion":        row.ocupacion,
            "lugar_trabajo":    row.lugar_trabajo,
            "total_consultas":  row.total_consultas,
        })}

    # ── Estadísticas ─────────────────────────────────────────────────────
    def summary(self) -> dict:
        total_c = self.db.execute(text("SELECT COUNT(*) AS n FROM consultas")).fetchone().n or 0
        total_p = (self.db.execute(text("SELECT COUNT(*) AS n FROM paciente")).fetchone().n
                   if self._tiene_paciente else
                   self.db.execute(text("SELECT COUNT(DISTINCT idpacientes) AS n FROM consultas WHERE idpacientes IS NOT NULL")).fetchone().n) or 0
        con_motivo = self.db.execute(
            text("SELECT COUNT(*) AS n FROM consultas WHERE motivoConsulta IS NOT NULL")
        ).fetchone().n or 0
        return {
            "type":                    "summary",
            "total_consultas":         total_c,
            "total_pacientes":         total_p,
            "con_motivo_registrado":   con_motivo,
            "tabla_paciente_activa":   self._tiene_paciente,
        }
