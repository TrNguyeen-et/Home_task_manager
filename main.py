import os
from datetime import date, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from fastapi.middleware.cors import CORSMiddleware

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


# ===================== MODEL =====================
class AssignRequest(BaseModel):
    date: str

class ApproveRequest(BaseModel):
    points_to_add: int

class TaskCreate(BaseModel):
    title: str
    duration_minutes: int = 30
    points: int = 5
    frequency: str = "daily"
    preferred_slot: Optional[str] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    duration_minutes: Optional[int] = None
    points: Optional[int] = None
    frequency: Optional[str] = None
    preferred_slot: Optional[str] = None


# ===================== HẰNG SỐ =====================
DAY_MAP = {
    0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
    4: "Thursday", 5: "Friday", 6: "Saturday"
}
SLOT_ORDER = ["Morning", "Afternoon", "Evening"]
SLOT_LABEL = {"Morning": "Sáng", "Afternoon": "Chiều", "Evening": "Tối"}
FREQ_LABEL = {"daily": "Hàng ngày", "weekly": "Hàng tuần", "monthly": "Hàng tháng"}


# ===================== SMART SCHEDULER =====================
def smart_scheduler(target_date: str, dry_run: bool = False):
    """
    Thuật toán phân việc thông minh có xét frequency:
    - daily: luôn có mỗi ngày
    - weekly: chỉ 1 lần/tuần (kiểm tra đã giao trong tuần chưa)
    - monthly: chỉ 1 lần/tháng (kiểm tra đã giao trong tháng chưa)
    """
    target = date.fromisoformat(target_date)
    day_name = DAY_MAP[target.weekday()]

    # --- Lấy thành viên ---
    members = supabase.table("profiles").select("id, full_name").eq("role", "member").execute().data
    if not members:
        return {"success": False, "message": "Không có thành viên nào!"}

    # --- Lấy lịch rảnh ---
    all_sched = supabase.table("schedules").select("user_id, slot, is_free").eq("day_of_week", day_name).eq("is_free", True).execute().data
    availability = {}
    for m in members:
        availability[m["id"]] = {"Morning": False, "Afternoon": False, "Evening": False}
    for s in all_sched:
        if s["user_id"] in availability and s["slot"] in SLOT_ORDER:
            availability[s["user_id"]][s["slot"]] = True

    # --- Lọc thành viên rảnh ---
    active_members = []
    member_info = {}
    for m in members:
        free_slots = [s for s in SLOT_ORDER if availability[m["id"]][s]]
        if free_slots:
            active_members.append(m["id"])
            member_info[m["id"]] = {
                "name": m["full_name"],
                "free_slots": free_slots,
                "total_minutes": 0,
                "task_count": 0
            }

    if not active_members:
        return {"success": False, "message": f"Không ai rảnh vào {day_name}!"}

    # --- Lấy TẤT CẢ tasks ---
    all_tasks = supabase.table("tasks").select("*").execute().data
    if not all_tasks:
        return {"success": False, "message": "Không có việc nào trong database!"}

    # --- Lấy việc ĐÃ giao cho ngày này (tránh trùng) ---
    existing_today = supabase.table("assignments").select("task_id, slot, user_id").eq("assigned_date", target_date).execute().data
    existing_task_ids = set(e["task_id"] for e in existing_today)
    existing_slots = {}
    for e in existing_today:
        slot = e.get("slot", "Morning")
        existing_slots[slot] = existing_slots.get(slot, 0) + 1

    # --- Tính tuần và tháng để kiểm tra frequency ---
    monday_of_week = target - timedelta(days=target.weekday())
    sunday_of_week = monday_of_week + timedelta(days=6)
    first_of_month = target.replace(day=1)
    if target.month == 12:
        last_of_month = target.replace(year=target.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last_of_month = target.replace(month=target.month + 1, day=1) - timedelta(days=1)

    # --- Lọc tasks theo frequency ---
    tasks_to_assign = []

    for task in all_tasks:
        # Bỏ qua nếu đã giao cho ngày này
        if task["id"] in existing_task_ids:
            continue

        freq = task.get("frequency", "daily")

        if freq == "daily":
            tasks_to_assign.append(task)

        elif freq == "weekly":
            # Kiểm tra đã giao trong tuần này chưa
            weekly_existing = supabase.table("assignments").select("id").eq("task_id", task["id"]).gte("assigned_date", monday_of_week.isoformat()).lte("assigned_date", sunday_of_week.isoformat()).execute().data
            if not weekly_existing:
                tasks_to_assign.append(task)

        elif freq == "monthly":
            # Kiểm tra đã giao trong tháng này chưa
            monthly_existing = supabase.table("assignments").select("id").eq("task_id", task["id"]).gte("assigned_date", first_of_month.isoformat()).lte("assigned_date", last_of_month.isoformat()).execute().data
            if not monthly_existing:
                tasks_to_assign.append(task)

    if not tasks_to_assign:
        return {"success": False, "message": f"Tất cả việc đã được giao cho ngày {target_date}!"}

    # --- Sắp xếp: có preferred_slot trước, rồi nặng trước ---
    tasks_to_assign.sort(key=lambda t: (
        0 if t.get("preferred_slot") else 1,
        -(t.get("duration_minutes", 0))
    ))

    # --- Phân bổ ---
    assignments = []
    unassigned = []

    for task in tasks_to_assign:
        duration = task.get("duration_minutes", 30)
        preferred = task.get("preferred_slot")

        # Tìm ca phù hợp
        best_slot = None
        min_load = float('inf')

        for slot in SLOT_ORDER:
            people_avail = [uid for uid in active_members if availability[uid][slot]]
            if not people_avail:
                continue
            slot_load = existing_slots.get(slot, 0)
            if preferred and slot == preferred:
                best_slot = slot
                break
            if slot_load < min_load:
                min_load = slot_load
                best_slot = slot

        if not best_slot:
            unassigned.append({"task": task["title"], "reason": "Không có ca phù hợp"})
            continue

        # Tìm người tải thấp nhất trong ca đó
        candidates = [uid for uid in active_members if availability[uid][best_slot]]
        if not candidates:
            unassigned.append({"task": task["title"], "reason": f"Không ai rảnh ca {SLOT_LABEL.get(best_slot, best_slot)}"})
            continue

        best_person = min(candidates, key=lambda uid: member_info[uid]["total_minutes"])

        assignments.append({
            "task_id": task["id"],
            "user_id": best_person,
            "assigned_date": target_date,
            "status": "pending",
            "slot": best_slot
        })

        member_info[best_person]["total_minutes"] += duration
        member_info[best_person]["task_count"] += 1
        existing_slots[best_slot] = existing_slots.get(best_slot, 0) + 1

    # --- Lưu (nếu không phải preview) ---
    result_assignments = []
    if assignments and not dry_run:
        res = supabase.table("assignments").insert(assignments).execute()
        result_assignments = res.data

        # Lấy thêm tên task và tên người để trả về
        if result_assignments:
            for a in result_assignments:
                task_data = next((t for t in all_tasks if t["id"] == a["task_id"]), {})
                a["task_title"] = task_data.get("title", "?")
                a["task_points"] = task_data.get("points", 0)
                a["task_duration"] = task_data.get("duration_minutes", 0)
                a["assigned_to"] = member_info.get(a["user_id"], {}).get("name", "?")

    # --- Log ---
    if not dry_run:
        try:
            supabase.table("schedule_logs").insert([{
                "scheduled_date": target_date,
                "assignments_count": len(assignments),
                "detail": {
                    "day": day_name,
                    "member_info": member_info,
                    "unassigned": unassigned,
                    "slots_used": existing_slots
                }
            }]).execute()
        except Exception:
            pass

    return {
        "success": len(assignments) > 0,
        "message": f"Đã phân {len(assignments)} việc cho {target_date}" if assignments else "Không có việc mới để phân.",
        "date": target_date,
        "day": day_name,
        "is_preview": dry_run,
        "assignments": result_assignments,
        "summary": member_info,
        "unassigned": unassigned,
        "slots_used": existing_slots,
        "already_existed": len(existing_task_ids)
    }


# ===================== API ROUTES =====================

@app.get("/")
def read_root():
    return {"message": "Home Task Manager API đang chạy!"}

@app.get("/api/config")
def get_public_config():
    return {"SUPABASE_URL": os.getenv("SUPABASE_URL"), "SUPABASE_KEY": os.getenv("SUPABASE_KEY")}


# ---------- TASK CRUD ----------

@app.get("/tasks")
def get_tasks():
    """Lấy danh sách tất cả công việc"""
    data = supabase.table("tasks").select("*").order("frequency").order("duration_minutes", desc=True).execute().data
    return {"tasks": data}

@app.post("/tasks")
def create_task(task: TaskCreate):
    """Tạo công việc mới"""
    if task.frequency not in ["daily", "weekly", "monthly"]:
        raise HTTPException(400, "frequency phải là daily/weekly/monthly")
    if task.preferred_slot and task.preferred_slot not in ["Morning", "Afternoon", "Evening"]:
        raise HTTPException(400, "preferred_slot phải là Morning/Afternoon/Evening")
    res = supabase.table("tasks").insert([{
        "title": task.title,
        "duration_minutes": task.duration_minutes,
        "points": task.points,
        "frequency": task.frequency,
        "preferred_slot": task.preferred_slot
    }]).execute()
    return {"message": "Đã tạo việc!", "task": res.data[0]}

@app.put("/tasks/{task_id}")
def update_task(task_id: str, task: TaskUpdate):
    """Cập nhật công việc"""
    updates = {}
    if task.title is not None: updates["title"] = task.title
    if task.duration_minutes is not None: updates["duration_minutes"] = task.duration_minutes
    if task.points is not None: updates["points"] = task.points
    if task.frequency is not None:
        if task.frequency not in ["daily", "weekly", "monthly"]:
            raise HTTPException(400, "frequency sai")
        updates["frequency"] = task.frequency
    if task.preferred_slot is not None:
        if task.preferred_slot not in ["Morning", "Afternoon", "Evening", ""]:
            raise HTTPException(400, "preferred_slot sai")
        updates["preferred_slot"] = task.preferred_slot if task.preferred_slot else None
    if not updates:
        raise HTTPException(400, "Không có gì để cập nhật")
    res = supabase.table("tasks").update(updates).eq("id", task_id).execute()
    if not res.data:
        raise HTTPException(404, "Không tìm thấy task")
    return {"message": "Đã cập nhật!", "task": res.data[0]}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """Xóa công việc"""
    res = supabase.table("tasks").delete().eq("id", task_id).execute()
    if not res.data:
        raise HTTPException(404, "Không tìm thấy task")
    return {"message": "Đã xóa!"}

@app.post("/tasks/seed-defaults")
def seed_defaults():
    """Chèn lại 9 việc mặc định"""
    defaults = [
        ("Rửa chén",     15, 2,  "daily",   "Afternoon"),
        ("Đổ rác",       5,  1,  "daily",   "Evening"),
        ("Nấu cơm",      20, 3,  "daily",   None),
        ("Lau nhà",      45, 8,  "weekly",  "Morning"),
        ("Mua đồ ăn",    30, 5,  "weekly",  "Morning"),
        ("Nấu ăn",       40, 7,  "weekly",  "Morning"),
        ("Giặt đồ",      20, 3,  "weekly",  "Morning"),
        ("Phơi đồ",      10, 1,  "weekly",  "Afternoon"),
        ("Rửa nhà tắm",  60, 10, "monthly", "Morning"),
    ]
    inserted = []
    for title, dur, pts, freq, slot in defaults:
        existing = supabase.table("tasks").select("id").eq("title", title).execute().data
        if not existing:
            res = supabase.table("tasks").insert([{"title": title, "duration_minutes": dur, "points": pts, "frequency": freq, "preferred_slot": slot}]).execute()
            if res.data: inserted.append(title)
    return {"message": f"Đã chèn {len(inserted)} việc mặc định.", "inserted": inserted}


# ---------- SCHEDULER ----------

@app.post("/preview-algorithm")
def preview_schedule(req: AssignRequest):
    """Xem trước kết quả phân việc (KHÔNG lưu)"""
    try:
        return smart_scheduler(req.date, dry_run=True)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/run-algorithm")
def run_schedule(req: AssignRequest):
    """Chạy phân việc và LƯU vào database"""
    try:
        return smart_scheduler(req.date, dry_run=False)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/weekly-schedule")
def get_weekly_schedule():
    """Lịch phân việc cả tuần"""
    try:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        results = {}
        for i in range(7):
            d = monday + timedelta(days=i)
            d_str = d.isoformat()
            data = supabase.table("assignments").select(
                "id, status, slot, tasks(title, points, duration_minutes, frequency), profiles(full_name)"
            ).eq("assigned_date", d_str).execute().data
            results[d_str] = {
                "day": DAY_MAP[d.weekday()],
                "date_label": d.strftime("%d/%m"),
                "is_today": d == today,
                "assignments": data or []
            }
        return {"week_start": monday.isoformat(), "days": results}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.delete("/clear-schedule/{target_date}")
def clear_schedule(target_date: str):
    """Xóa toàn bộ phân việc của 1 ngày (để chạy lại)"""
    res = supabase.table("assignments").delete().eq("assigned_date", target_date).execute()
    return {"message": f"Đã xóa {len(res.data)} việc của ngày {target_date}"}


# ---------- DUYỆT & ĐIỂM ----------

@app.post("/approve-task/{assignment_id}")
def approve_task(assignment_id: str, req: ApproveRequest):
    try:
        supabase.table("assignments").update({"status": "completed"}).eq("id", assignment_id).execute()
        assign = supabase.table("assignments").select("user_id").eq("id", assignment_id).single().execute()
        user_id = assign.data["user_id"]
        profile = supabase.table("profiles").select("total_points").eq("id", user_id).single().execute()
        current = profile.data.get("total_points", 0)
        supabase.table("profiles").update({"total_points": current + req.points_to_add}).eq("id", user_id).execute()
        return {"message": "Đã duyệt và cộng điểm!"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------- CHI TIÊU ----------

@app.get("/calculate-settlement")
def calculate_settlement():
    expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
    users = supabase.table("profiles").select("id, full_name").eq("role", "member").execute().data
    n = len(users)
    if n == 0:
        return {"total": 0, "each_must_pay": 0, "summary": {}}
    total = sum(float(e["amount"]) for e in expenses)
    fair = total / n
    bal = {u["id"]: {"name": u["full_name"], "spent": 0, "net": 0} for u in users}
    for e in expenses:
        if e["payer_id"] in bal:
            bal[e["payer_id"]]["spent"] += float(e["amount"])
    for uid in bal:
        bal[uid]["net"] = bal[uid]["spent"] - fair
    return {"total": total, "each_must_pay": fair, "summary": bal}

@app.post("/settle-month")
def settle_month():
    try:
        expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
        users = supabase.table("profiles").select("id, full_name").eq("role", "member").execute().data
        n = len(users)
        if n == 0 or not expenses:
            return {"message": "Không có dữ liệu."}
        total = sum(float(e["amount"]) for e in expenses)
        fair = total / n
        summary = {}
        for u in users:
            spent = sum(float(e["amount"]) for e in expenses if e["payer_id"] == u["id"])
            summary[u["full_name"]] = {"chi": spent, "can_tra": fair, "cong_no": round(spent - fair, 2)}
        supabase.table("expenses").update({"is_settled": True}).eq("is_settled", False).execute()
        try:
            supabase.table("settlement_history").insert([{"total": total, "per_person": fair, "summary": summary}]).execute()
        except Exception:
            pass
        return {"message": "Chốt sổ thành công!", "total": total, "per_person": fair, "detail": summary}
    except Exception as e:
        raise HTTPException(500, str(e))