import os
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Thiếu Supabase Key trong .env!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="Home Task Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AssignRequest(BaseModel):
    date: str

class ApproveRequest(BaseModel):
    points_to_add: int

@app.get("/")
def read_root():
    return {"message": "Hệ thống Quản lý việc nhà đang chạy!"}

@app.get("/api/config")
def get_public_config():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_KEY": os.getenv("SUPABASE_KEY")
    }

@app.post("/run-algorithm")
def assign_daily_tasks(req: AssignRequest):
    try:
        tasks = supabase.table("tasks").select("*").execute().data
        if not tasks:
            return {"message": "Không có công việc nào"}
        tasks.sort(key=lambda x: x.get('duration_minutes', 0), reverse=True)

        users = supabase.table("profiles").select("*").eq("role", "member").execute().data
        if not users:
            return {"message": "Không có thành viên nào!"}

        workload = {user['id']: 0 for user in users}
        assignments = []

        for task in tasks:
            candidates = users
            if not candidates:
                continue
            best = min(candidates, key=lambda u: workload[u['id']])
            assignments.append({
                "task_id": task['id'],
                "user_id": best['id'],
                "assigned_date": req.date,
                "status": "pending"
            })
            workload[best['id']] += task.get('duration_minutes', 0)

        if assignments:
            res = supabase.table("assignments").insert(assignments).execute()
            return {
                "message": "Đã chia việc xong!",
                "workload_summary": workload,
                "assigned_count": len(res.data)
            }
        return {"message": "Không có task nào được phân chia."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/approve-task/{assignment_id}")
def approve_task(assignment_id: str, req: ApproveRequest):
    try:
        supabase.table("assignments").update({"status": "completed"}).eq("id", assignment_id).execute()
        assign_data = supabase.table("assignments").select("user_id").eq("id", assignment_id).single().execute()
        user_id = assign_data.data["user_id"]
        user_data = supabase.table("profiles").select("total_points").eq("id", user_id).single().execute()
        current = user_data.data.get("total_points", 0)
        supabase.table("profiles").update({"total_points": current + req.points_to_add}).eq("id", user_id).execute()
        return {"message": "Đã duyệt và cộng điểm thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/calculate-settlement")
def calculate_settlement():
    expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
    users = supabase.table("profiles").select("id, full_name").eq("role", "member").execute().data
    num_users = len(users)
    if num_users == 0:
        return {"total": 0, "each_must_pay": 0, "summary": {}}

    total_spent = sum(float(e['amount']) for e in expenses)
    fair_share = total_spent / num_users

    balances = {u['id']: {"name": u['full_name'], "spent": 0, "net": 0} for u in users}
    for e in expenses:
        if e['payer_id'] in balances:
            balances[e['payer_id']]['spent'] += float(e['amount'])

    for uid in balances:
        balances[uid]['net'] = balances[uid]['spent'] - fair_share

    return {"total": total_spent, "each_must_pay": fair_share, "summary": balances}

# *** THÊM MỚI: Endpoint chốt sổ có lưu lịch sử ***
@app.post("/settle-month")
def settle_month():
    try:
        # 1. Tính toán trước khi chốt
        expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
        users = supabase.table("profiles").select("id, full_name").eq("role", "member").execute().data
        num_users = len(users)
        if num_users == 0 or not expenses:
            return {"message": "Không có dữ liệu để chốt sổ."}

        total = sum(float(e['amount']) for e in expenses)
        fair = total / num_users

        summary = {}
        for u in users:
            spent = sum(float(e['amount']) for e in expenses if e['payer_id'] == u['id'])
            summary[u['full_name']] = {"chi": spent, "can_tra": fair, "cong_no": round(spent - fair, 2)}

        # 2. Đánh dấu đã chốt
        supabase.table("expenses").update({"is_settled": True}).eq("is_settled", False).execute()

        # 3. Lưu lịch sử chốt sổ (cần tạo bảng 'settlement_history' trong Supabase)
        # CREATE TABLE settlement_history (id UUID DEFAULT gen_random_uuid(), settled_at TIMESTAMPTZ DEFAULT now(), total NUMERIC, per_person NUMERIC, summary JSONB);
        try:
            supabase.table("settlement_history").insert([{
                "total": total,
                "per_person": fair,
                "summary": summary
            }]).execute()
        except Exception:
            pass  # Nếu chưa tạo bảng thì bỏ qua, không block luồng

        return {"message": "Chốt sổ thành công!", "total": total, "per_person": fair, "detail": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))