import os
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from fastapi.middleware.cors import CORSMiddleware

# ==========================================
# 1. CẤU HÌNH MÔI TRƯỜNG & DATABASE
# ==========================================
load_dotenv() 

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("⚠️ Thiếu Supabase API Key trong file .env!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
print("✅ Đã kết nối Database thành công!")

# ==========================================
# 2. KHỞI TẠO FASTAPI
# ==========================================
app = FastAPI(title="Hệ Thống Quản Lý Việc Nhà API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- KHAI BÁO MODEL DỮ LIỆU ---
class AssignRequest(BaseModel):
    date: str # VD: "2026-04-20"

class ApproveRequest(BaseModel):
    points_to_add: int

# ==========================================
# 3. DANH SÁCH CÁC API CỐT LÕI
# ==========================================

@app.get("/")
def read_root():
    return {"message": "Hệ thống Quản lý việc nhà an toàn đã khởi động!"}

# --- THÊM LẠI ĐOẠN NÀY ĐỂ TRẢ KEY CHO GIAO DIỆN WEB ---
@app.get("/api/config")
def get_public_config():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_KEY": os.getenv("SUPABASE_KEY")
    }

# --- API 1: CHẠY THUẬT TOÁN CHIA VIỆC ---
@app.post("/run-algorithm")
def assign_daily_tasks(req: AssignRequest):
    try:
        # A. Lấy danh sách việc cần làm (Ưu tiên việc nặng phân trước)
        tasks = supabase.table("tasks").select("*").execute().data
        if not tasks:
            return {"message": "Không có công việc nào trong Database"}
        tasks.sort(key=lambda x: x.get('duration_minutes', 0), reverse=True)

        # B. Lấy danh sách thành viên (CHỈ LẤY MEMBER, KHÔNG CHIA VIỆC CHO ADMIN)
        users = supabase.table("profiles").select("*").eq("role", "member").execute().data
        if not users:
            return {"message": "Không có thành viên nào để giao việc!"}

        # Khởi tạo bộ cân bằng tải
        workload = {user['id']: 0 for user in users}
        assignments = []

        # C. Logic Greedy (Tham lam)
        for task in tasks:
            candidates = users # Tạm thời ai cũng làm được
            
            # Nếu bác có bảng task_eligibility thì mở block code này ra:
            # eligible_users = supabase.table("task_eligibility").select("user_id").eq("task_id", task['id']).execute().data
            # allowed_ids = [row['user_id'] for row in eligible_users]
            # if allowed_ids:
            #     candidates = [u for u in users if u['id'] in allowed_ids]
            
            if not candidates:
                continue

            # Giao việc cho đứa đang có workload thấp nhất
            best_candidate = min(candidates, key=lambda u: workload[u['id']])
            
            assignments.append({
                "task_id": task['id'],
                "user_id": best_candidate['id'],
                "assigned_date": req.date,
                "status": "pending"
            })
            workload[best_candidate['id']] += task.get('duration_minutes', 0)

        # D. Lưu vào Database
        if assignments:
            insert_res = supabase.table("assignments").insert(assignments).execute()
            return {
                "message": "Đã chia việc xong!", 
                "workload_summary": workload,
                "assigned_data": insert_res.data
            }

        return {"message": "Không có task nào được phân chia."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- API 2: ADMIN DUYỆT CÔNG VIỆC VÀ CỘNG ĐIỂM ---
@app.post("/approve-task/{assignment_id}")
def approve_task(assignment_id: str, req: ApproveRequest):
    try:
        # 1. Đổi status công việc thành "completed" (Đã hoàn thành)
        supabase.table("assignments").update({"status": "completed"}).eq("id", assignment_id).execute()
        
        # 2. Tìm xem ai là người làm công việc này
        assign_data = supabase.table("assignments").select("user_id").eq("id", assignment_id).single().execute()
        user_id = assign_data.data["user_id"]
        
        # 3. Lấy điểm hiện tại của người đó và cộng thêm
        user_data = supabase.table("profiles").select("total_points").eq("id", user_id).single().execute()
        current_points = user_data.data.get("total_points", 0)
        
        supabase.table("profiles").update({"total_points": current_points + req.points_to_add}).eq("id", user_id).execute()
        
        return {"message": "✅ Đã duyệt công việc và cộng điểm thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- API 3: QUYẾT TOÁN TIỀN NONG ---
@app.get("/calculate-settlement")
def calculate_settlement():
    expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
    users = supabase.table("profiles").select("id, full_name").eq("role", "member").execute().data
    num_users = len(users)
    
    total_spent = sum(float(e['amount']) for e in expenses)
    fair_share = total_spent / num_users if num_users > 0 else 0
    
    balances = {u['id']: {"name": u['full_name'], "spent": 0, "net": 0} for u in users}
    for e in expenses:
        if e['payer_id'] in balances:
            balances[e['payer_id']]['spent'] += float(e['amount'])
        
    for uid in balances:
        balances[uid]['net'] = balances[uid]['spent'] - fair_share
        
    return {
        "total": total_spent,
        "each_must_pay": fair_share,
        "summary": balances
    }