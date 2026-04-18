import os
import random
import string
from datetime import date, timedelta
from typing import Optional
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

app = FastAPI(title="Hệ Thống Quản Lý Việc Nhà API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# 2. KHAI BÁO MODEL DỮ LIỆU (PYDANTIC)
# ==========================================
class AssignRequest(BaseModel):
    date: str
    household_id: str

class ApproveRequest(BaseModel):
    points_to_add: int

class TaskCreate(BaseModel):
    title: str
    duration_minutes: int = 30
    points: int = 5
    frequency: str = "daily"
    preferred_slot: Optional[str] = None
    household_id: str = ""

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    duration_minutes: Optional[int] = None
    points: Optional[int] = None
    frequency: Optional[str] = None
    preferred_slot: Optional[str] = None

class CreateHouseRequest(BaseModel):
    name: str
    user_id: str

class JoinHouseRequest(BaseModel):
    join_code: str
    user_id: str


# ==========================================
# 3. HẰNG SỐ & HÀM HỖ TRỢ
# ==========================================
DAY_MAP = {
    0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
    4: "Thursday", 5: "Friday", 6: "Saturday"
}
SLOT_ORDER = ["Morning", "Afternoon", "Evening"]
SLOT_LABEL = {"Morning": "Sáng", "Afternoon": "Chiều", "Evening": "Tối"}
FREQ_LABEL = {"daily": "Hàng ngày", "weekly": "Hàng tuần", "monthly": "Hàng tháng"}

def generate_join_code():
    """Tạo mã gia đình ngẫu nhiên 6 ký tự"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


# ==========================================
# 4. CÁC API CƠ BẢN
# ==========================================
@app.get("/")
def read_root():
    return {"message": "Hệ thống Quản lý việc nhà đang chạy!"}

@app.get("/api/config")
def get_public_config():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL"),
        "SUPABASE_KEY": os.getenv("SUPABASE_KEY")
    }


# ==========================================
# 5. API QUẢN LÝ HỘ GIA ĐÌNH (HOUSEHOLDS)
# ==========================================
@app.post("/api/households")
def create_household(req: CreateHouseRequest):
    """Tạo nhà mới, set người tạo làm Admin và tự động chèn 9 việc mặc định"""
    try:
        code = generate_join_code()
        
        # 1. Tạo nhà
        res = supabase.table("households").insert([{"name": req.name, "join_code": code}]).execute()
        if not res.data:
            raise HTTPException(500, "Lỗi khi tạo nhà")
        
        house_id = res.data[0]["id"]
        
        # 2. Cập nhật profile của người tạo thành Admin của nhà này
        supabase.table("profiles").update({
            "household_id": house_id, 
            "role": "admin"
        }).eq("id", req.user_id).execute()
        
        # 3. Tự động chèn 9 việc mặc định cho nhà mới
        defaults = [
            ("Rửa chén", 15, 2, "daily", "Afternoon"),
            ("Đổ rác", 5, 1, "daily", "Evening"),
            ("Nấu cơm", 20, 3, "daily", None),
            ("Lau nhà", 45, 8, "weekly", "Morning"),
            ("Mua đồ ăn", 30, 5, "weekly", "Morning"),
            ("Nấu ăn", 40, 7, "weekly", "Morning"),
            ("Giặt đồ", 20, 3, "weekly", "Morning"),
            ("Phơi đồ", 10, 1, "weekly", "Afternoon"),
            ("Rửa nhà tắm", 60, 10, "monthly", "Morning"),
        ]
        
        for title, dur, pts, freq, slot in defaults:
            supabase.table("tasks").insert([{
                "title": title, 
                "duration_minutes": dur, 
                "points": pts, 
                "frequency": freq, 
                "preferred_slot": slot, 
                "household_id": house_id
            }]).execute()

        return {
            "message": "Đã tạo nhà thành công!", 
            "household_id": house_id, 
            "join_code": code, 
            "household_name": req.name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/households/join")
def join_household(req: JoinHouseRequest):
    """Tham gia vào nhà bằng mã mời"""
    try:
        # Tìm nhà theo mã (chuyển hết thành chữ hoa để không phân biệt hoa/thường)
        house_res = supabase.table("households").select("*").eq("join_code", req.join_code.strip().upper()).execute()
        if not house_res.data:
            raise HTTPException(404, "Mã gia đình không tồn tại!")
        
        house = house_res.data[0]
        
        # Cập nhật profile: gán vào nhà này và set role thành member
        supabase.table("profiles").update({
            "household_id": house["id"], 
            "role": "member"
        }).eq("id", req.user_id).execute()

        return {
            "message": f"Đã tham gia {house['name']}!", 
            "household_id": house["id"], 
            "household_name": house["name"]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/households/my")
def get_my_household(user_id: str):
    """Lấy thông tin nhà của user hiện tại"""
    data = supabase.table("profiles").select("household_id, households(name, join_code)").eq("id", user_id).single().execute()
    return data.data

@app.post("/api/households/leave")
def leave_household(user_id: str):
    """Rời khỏi nhà hiện tại"""
    supabase.table("profiles").update({"household_id": None, "role": "member"}).eq("id", user_id).execute()
    return {"message": "Đã rời khỏi nhà."}


# ==========================================
# 6. API QUẢN LÝ CÔNG VIỆC (TASKS)
# ==========================================
@app.get("/tasks")
def get_tasks(household_id: str = ""):
    """Lấy danh sách việc. Nếu có household_id thì chỉ lấy việc của nhà đó"""
    q = supabase.table("tasks").select("*")
    if household_id:
        q = q.eq("household_id", household_id)
    return {"tasks": q.order("frequency").order("duration_minutes", desc=True).execute().data}

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
        "preferred_slot": task.preferred_slot,
        "household_id": task.household_id or None
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
def seed_defaults(household_id: str = ""):
    """Chèn lại 9 việc mặc định (không xóa việc đã có)"""
    defaults = [
        ("Rửa chén", 15, 2, "daily", "Afternoon"),
        ("Đổ rác", 5, 1, "daily", "Evening"),
        ("Nấu cơm", 20, 3, "daily", None),
        ("Lau nhà", 45, 8, "weekly", "Morning"),
        ("Mua đồ ăn", 30, 5, "weekly", "Morning"),
        ("Nấu ăn", 40, 7, "weekly", "Morning"),
        ("Giặt đồ", 20, 3, "weekly", "Morning"),
        ("Phơi đồ", 10, 1, "weekly", "Afternoon"),
        ("Rửa nhà tắm", 60, 10, "monthly", "Morning"),
    ]
    inserted = []
    for title, dur, pts, freq, slot in defaults:
        q = supabase.table("tasks").select("id").eq("title", title)
        if household_id:
            q = q.eq("household_id", household_id)
            
        if not q.execute().data:
            row = {"title": title, "duration_minutes": dur, "points": pts, "frequency": freq, "preferred_slot": slot}
            if household_id:
                row["household_id"] = household_id
            r = supabase.table("tasks").insert([row]).execute()
            if r.data:
                inserted.append(title)
    return {"message": f"Đã chèn {len(inserted)} việc mặc định.", "inserted": inserted}


# ==========================================
# 7. CORE: THUẬT TOÁN PHÂN VIỆC THÔNG MINH
# ==========================================
def smart_scheduler(target_date: str, household_id: str, dry_run: bool = False):
    """
    Thuật toán phân việc có xét frequency & lịch rảnh:
    - daily: luôn có mỗi ngày
    - weekly: chỉ 1 lần/tuần (kiểm tra đã giao trong tuần chưa)
    - monthly: chỉ 1 lần/tháng (kiểm tra đã giao trong tháng chưa)
    """
    target = date.fromisoformat(target_date)
    day_name = DAY_MAP[target.weekday()]

    # --- Lấy thành viên THUỘC nhà này ---
    members = supabase.table("profiles").select("id, full_name").eq("role", "member").eq("household_id", household_id).execute().data
    if not members:
        return {"success": False, "message": "Không có thành viên nào trong nhà này!"}

    # --- Lấy lịch rảnh của các thành viên cho ngày này ---
    all_sched = supabase.table("schedules").select("user_id, slot, is_free").eq("day_of_week", day_name).eq("is_free", True).execute().data
    availability = {}
    for m in members:
        availability[m["id"]] = {"Morning": False, "Afternoon": False, "Evening": False}
    for s in all_sched:
        if s["user_id"] in availability and s["slot"] in SLOT_ORDER:
            availability[s["user_id"]][s["slot"]] = True

    # Lọc những người CÓ ÍT NHẤT 1 CA rảnh
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
        return {"success": False, "message": f"Không ai rảnh vào {day_name} ({target_date})!"}

    # --- Lấy tasks THUỘC nhà này ---
    all_tasks = supabase.table("tasks").select("*").eq("household_id", household_id).execute().data
    if not all_tasks:
        return {"success": False, "message": "Không có việc nào trong database!"}

    # --- Lấy việc ĐÃ giao cho ngày này (tránh trùng lặp) ---
    existing_today = supabase.table("assignments").select("task_id, slot, user_id").eq("assigned_date", target_date).execute().data
    existing_task_ids = set(e["task_id"] for e in existing_today)
    existing_slots = {}
    for e in existing_today:
        slot = e.get("slot", "Morning")
        existing_slots[slot] = existing_slots.get(slot, 0) + 1

    # --- Tính khoảng thời gian của tuần và tháng hiện tại ---
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
        if task["id"] in existing_task_ids:
            continue

        freq = task.get("frequency", "daily")

        if freq == "daily":
            tasks_to_assign.append(task)
        elif freq == "weekly":
            # Kiểm tra đã giao trong tuần này chưa
            weekly_exists = supabase.table("assignments").select("id").eq("task_id", task["id"]).gte("assigned_date", monday_of_week.isoformat()).lte("assigned_date", sunday_of_week.isoformat()).execute().data
            if not weekly_exists:
                tasks_to_assign.append(task)
        elif freq == "monthly":
            # Kiểm tra đã giao trong tháng này chưa
            monthly_exists = supabase.table("assignments").select("id").eq("task_id", task["id"]).gte("assigned_date", first_of_month.isoformat()).lte("assigned_date", last_of_month.isoformat()).execute().data
            if not monthly_exists:
                tasks_to_assign.append(task)

    if not tasks_to_assign:
        return {"success": False, "message": f"Tất cả việc đã được giao cho ngày {target_date}!", "already_existed": len(existing_task_ids)}

    # Sắp xếp: Ưu tiên việc có preferred_slot, rồi việc nặng trước
    tasks_to_assign.sort(key=lambda t: (
        0 if t.get("preferred_slot") else 1,
        -(t.get("duration_minutes", 0))
    ))

    # --- PHÂN BỔ VIỆC ---
    assignments = []
    unassigned = []

    for task in tasks_to_assign:
        duration = task.get("duration_minutes", 30)
        preferred = task.get("preferred_slot")

        # Tìm ca phù hợp nhất
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

        # Cập nhật tải
        member_info[best_person]["total_minutes"] += duration
        member_info[best_person]["task_count"] += 1
        existing_slots[best_slot] = existing_slots.get(best_slot, 0) + 1

    # --- LƯU VÀO DATABASE (nếu không phải preview) ---
    result_assignments = []
    if assignments and not dry_run:
        res = supabase.table("assignments").insert(assignments).execute()
        result_assignments = res.data

        # Gắn thêm thông tin tên việc và tên người để frontend hiển thị dễ dàng
        if result_assignments:
            for a in result_assignments:
                task_data = next((t for t in all_tasks if t["id"] == a["task_id"]), {})
                a["task_title"] = task_data.get("title", "?")
                a["task_points"] = task_data.get("points", 0)
                a["task_duration"] = task_data.get("duration_minutes", 0)
                a["assigned_to"] = member_info.get(a["user_id"], {}).get("name", "?")

    # Lưu log
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


# ==========================================
# 8. API CALL SCHEDULER
# ==========================================
@app.post("/preview-algorithm")
def preview_schedule(req: AssignRequest):
    """Xem trước kết quả (KHÔNG lưu)"""
    try:
        return smart_scheduler(req.date, req.household_id, dry_run=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/run-algorithm")
def run_schedule(req: AssignRequest):
    """Chạy phân việc và LƯU vào database"""
    try:
        return smart_scheduler(req.date, req.household_id, dry_run=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/weekly-schedule")
def get_weekly_schedule(household_id: str = ""):
    """Trả về lịch phân việc cả tuần"""
    try:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        
        # Lấy danh sách ID thành viên của nhà này để filter
        members = supabase.table("profiles").select("id").eq("household_id", household_id).execute().data
        member_ids = [m["id"] for m in members]
        
        results = {}
        for i in range(7):
            d = monday + timedelta(days=i)
            d_str = d.isoformat()
            
            q = supabase.table("assignments").select(
                "id, status, slot, tasks(title, points, duration_minutes, frequency), profiles(full_name)"
            ).eq("assigned_date", d_str)
            
            # Chỉ lấy việc của thành viên trong nhà này
            if member_ids:
                q = q.in_("user_id", member_ids)
                
            data = q.execute().data
            results[d_str] = {
                "day": DAY_MAP[d.weekday()],
                "date_label": d.strftime("%d/%m"),
                "is_today": d == today,
                "assignments": data or []
            }
        return {"week_start": monday.isoformat(), "days": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/clear-schedule/{target_date}")
def clear_schedule(target_date: str):
    """Xóa toàn bộ phân việc của 1 ngày (để chạy lại)"""
    res = supabase.table("assignments").delete().eq("assigned_date", target_date).execute()
    return {"message": f"Đã xóa {len(res.data)} việc của ngày {target_date}"}


# ==========================================
# 9. API DUYỆT & CỘNG ĐIỂM
# ==========================================
@app.post("/approve-task/{assignment_id}")
def approve_task(assignment_id: str, req: ApproveRequest):
    """Admin duyệt task và cộng điểm cho thành viên"""
    try:
        # 1. Đổi status thành completed
        supabase.table("assignments").update({"status": "completed"}).eq("id", assignment_id).execute()
        
        # 2. Tìm ai là người làm
        assign_data = supabase.table("assignments").select("user_id").eq("id", assignment_id).single().execute()
        user_id = assign_data.data["user_id"]
        
        # 3. Lấy điểm hiện tại và cộng thêm
        user_data = supabase.table("profiles").select("total_points").eq("id", user_id).single().execute()
        current_points = user_data.data.get("total_points", 0)
        
        supabase.table("profiles").update({"total_points": current_points + req.points_to_add}).eq("id", user_id).execute()
        
        return {"message": "✅ Đã duyệt công việc và cộng điểm thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 10. API QUẢN LÝ CHI TIÊU
# ==========================================
@app.get("/calculate-settlement")
def calculate_settlement(household_id: str = ""):
    """Tính toán cân đối chi tiêu của các thành viên"""
    expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
    
    q = supabase.table("profiles").select("id, full_name").eq("role", "member")
    if household_id:
        q = q.eq("household_id", household_id)
    users = q.execute().data
    
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
        
    return {
        "total": total_spent,
        "each_must_pay": fair_share,
        "summary": balances
    }

@app.post("/settle-month")
def settle_month(household_id: str = ""):
    """Chốt sổ tháng: Đánh dấu expenses đã quyết toán"""
    try:
        expenses = supabase.table("expenses").select("*").eq("is_settled", False).execute().data
        
        q = supabase.table("profiles").select("id, full_name").eq("role", "member")
        if household_id:
            q = q.eq("household_id", household_id)
        users = q.execute().data
        
        num_users = len(users)
        if num_users == 0 or not expenses:
            return {"message": "Không có dữ liệu để chốt sổ."}
        
        total_spent = sum(float(e['amount']) for e in expenses)
        fair_share = total_spent / num_users
        
        summary = {}
        for u in users:
            spent = sum(float(e['amount']) for e in expenses if e['payer_id'] == u['id'])
            summary[u['full_name']] = {
                "chi": spent, 
                "can_tra": fair_share, 
                "cong_no": round(spent - fair_share, 2)
            }
            
        # Đánh dấu đã chốt
        supabase.table("expenses").update({"is_settled": True}).eq("is_settled", False).execute()
        
        # Lưu lịch sử (nếu có bảng)
        try:
            supabase.table("settlement_history").insert([{
                "total": total_spent, 
                "per_person": fair_share, 
                "summary": summary
            }]).execute()
        except Exception:
            pass
            
        return {"message": "Chốt sổ thành công!", "total": total_spent, "per_person": fair_share, "detail": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))