"""
Employee Management System — FastAPI backend.

Uses OOP patterns with a EmployeeRepository class for all DB access,
Pydantic models for input validation, and structured exception handling.
"""

import os
import math
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")


def get_conn():
    """Return a new psycopg2 connection with RealDictCursor."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EmployeeInput(BaseModel):
    firstName: str
    lastName: str
    email: str
    phone: Optional[str] = None
    department: str
    position: str
    salary: Optional[float] = None
    hireDate: date
    status: str = "active"
    address: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        if v not in ("active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        return v

    @field_validator("firstName", "lastName", "department", "position")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty")
        return v


class EmployeeUpdate(BaseModel):
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    salary: Optional[float] = None
    hireDate: Optional[date] = None
    status: Optional[str] = None
    address: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        return v


# ---------------------------------------------------------------------------
# Repository — all DB logic lives here (OOP pattern)
# ---------------------------------------------------------------------------

class EmployeeRepository:
    """Encapsulates all database operations for employee records."""

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: dict) -> dict:
        """Convert DB row to API-friendly dict (snake_case → camelCase, types)."""
        return {
            "id":         row["id"],
            "firstName":  row["first_name"],
            "lastName":   row["last_name"],
            "email":      row["email"],
            "phone":      row["phone"],
            "department": row["department"],
            "position":   row["position"],
            "salary":     float(row["salary"]) if row["salary"] is not None else None,
            "hireDate":   str(row["hire_date"]),
            "status":     row["status"],
            "address":    row["address"],
            "createdAt":  row["created_at"].isoformat() if row["created_at"] else None,
            "updatedAt":  row["updated_at"].isoformat() if row["updated_at"] else None,
        }

    # ---- CRUD --------------------------------------------------------------

    def list(
        self,
        search: Optional[str] = None,
        department: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """Return all employees, optionally filtered."""
        clauses = []
        params = []

        if search:
            clauses.append(
                "(first_name ILIKE %s OR last_name ILIKE %s "
                "OR email ILIKE %s OR position ILIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])

        if department:
            clauses.append("department = %s")
            params.append(department)

        if status:
            clauses.append("status = %s")
            params.append(status)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM employees {where} ORDER BY created_at DESC"

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def create(self, data: EmployeeInput) -> dict:
        """Insert a new employee and return the created record."""
        sql = """
            INSERT INTO employees
                (first_name, last_name, email, phone, department, position,
                 salary, hire_date, status, address)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        params = (
            data.firstName, data.lastName, data.email, data.phone,
            data.department, data.position,
            str(data.salary) if data.salary is not None else None,
            str(data.hireDate), data.status, data.address,
        )
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
                return self._row_to_dict(cur.fetchone())
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(status_code=400, detail="Email already exists")

    def get(self, employee_id: int) -> dict:
        """Fetch a single employee or raise 404."""
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM employees WHERE id = %s", (employee_id,))
            row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        return self._row_to_dict(row)

    def update(self, employee_id: int, data: EmployeeUpdate) -> dict:
        """Apply partial update to an employee record."""
        fields = data.model_dump(exclude_none=True)
        if not fields:
            return self.get(employee_id)

        # Map camelCase → snake_case column names
        col_map = {
            "firstName": "first_name",
            "lastName":  "last_name",
            "hireDate":  "hire_date",
        }
        set_parts = []
        params = []
        for key, value in fields.items():
            col = col_map.get(key, key)
            set_parts.append(f"{col} = %s")
            if isinstance(value, date):
                params.append(str(value))
            elif key == "salary":
                params.append(str(value))
            else:
                params.append(value)

        set_parts.append("updated_at = NOW()")
        params.append(employee_id)

        sql = f"UPDATE employees SET {', '.join(set_parts)} WHERE id = %s RETURNING *"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        return self._row_to_dict(row)

    def delete(self, employee_id: int) -> bool:
        """Delete an employee; raise 404 if not found."""
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM employees WHERE id = %s RETURNING id", (employee_id,)
            )
            conn.commit()
            deleted = cur.fetchone()
        if deleted is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        return True


class DepartmentRepository:
    """Department-level queries."""

    def list(self) -> list[dict]:
        sql = """
            SELECT department AS name, COUNT(*)::int AS "employeeCount"
            FROM employees
            GROUP BY department
            ORDER BY department
        """
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


class StatsRepository:
    """Aggregate statistics queries."""

    def summary(self) -> dict:
        sql = """
            SELECT
                COUNT(*)::int                                          AS total,
                COUNT(*) FILTER (WHERE status = 'active')::int        AS active,
                COUNT(*) FILTER (WHERE status = 'inactive')::int      AS inactive,
                COUNT(DISTINCT department)::int                        AS depts,
                AVG(salary::numeric)                                   AS avg_salary,
                COUNT(*) FILTER (
                    WHERE DATE_TRUNC('month', hire_date::date)
                        = DATE_TRUNC('month', CURRENT_DATE)
                )::int AS new_this_month
            FROM employees
        """
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()

        avg = float(row["avg_salary"]) if row["avg_salary"] is not None else None
        return {
            "totalEmployees":    row["total"],
            "activeEmployees":   row["active"],
            "inactiveEmployees": row["inactive"],
            "departments":       row["depts"],
            "avgSalary":         avg,
            "newHiresThisMonth": row["new_this_month"],
        }

    def by_department(self) -> list[dict]:
        sql = """
            SELECT
                department,
                COUNT(*)::int        AS count,
                AVG(salary::numeric) AS avg_salary
            FROM employees
            GROUP BY department
            ORDER BY department
        """
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [
                {
                    "department": r["department"],
                    "count":      r["count"],
                    "avgSalary":  float(r["avg_salary"]) if r["avg_salary"] else None,
                }
                for r in cur.fetchall()
            ]

    def recent_hires(self, limit: int = 5) -> list[dict]:
        sql = "SELECT * FROM employees ORDER BY hire_date DESC LIMIT %s"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (limit,))
            repo = EmployeeRepository()
            return [repo._row_to_dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Singleton repositories
# ---------------------------------------------------------------------------

employees_repo   = EmployeeRepository()
departments_repo = DepartmentRepository()
stats_repo       = StatsRepository()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify DB is reachable on startup
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        raise RuntimeError(f"Cannot connect to database: {exc}") from exc
    yield


app = FastAPI(title="Employee Management System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Health ----------------------------------------------------------------

@app.get("/api/healthz")
def health_check():
    return {"status": "ok"}

# ---- Employees -------------------------------------------------------------

@app.get("/api/employees")
def list_employees(
    search:     Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    status:     Optional[str] = Query(None),
):
    return employees_repo.list(search=search, department=department, status=status)


@app.post("/api/employees", status_code=201)
def create_employee(data: EmployeeInput):
    return employees_repo.create(data)


@app.get("/api/employees/{employee_id}")
def get_employee(employee_id: int):
    return employees_repo.get(employee_id)


@app.patch("/api/employees/{employee_id}")
def update_employee(employee_id: int, data: EmployeeUpdate):
    return employees_repo.update(employee_id, data)


@app.delete("/api/employees/{employee_id}")
def delete_employee(employee_id: int):
    employees_repo.delete(employee_id)
    return {"success": True}

# ---- Departments -----------------------------------------------------------

@app.get("/api/departments")
def list_departments():
    return departments_repo.list()

# ---- Stats -----------------------------------------------------------------

@app.get("/api/stats/summary")
def get_stats_summary():
    return stats_repo.summary()


@app.get("/api/stats/by-department")
def get_stats_by_department():
    return stats_repo.by_department()


@app.get("/api/stats/recent-hires")
def get_recent_hires():
    return stats_repo.recent_hires()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
