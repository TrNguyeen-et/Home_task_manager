import os, random, string
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
    raise ValueError("Thiếu Supabase Key!")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="Home Task Manager API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ===================== MODEL =====================
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

# ===================== HẰNG SỐ =====================
DAY_MAP = {0:"Sunday",1:"Monday",2:"Tuesday",3:"Wednesday",4:"Thursday",5:"Friday",6:"Saturday"}
SLOT_ORDER = ["Morning","Afternoon","Evening"]
SLOT_LABEL = {"Morning":"Sáng","Afternoon":"Chiều","Evening":"Tối"}
FREQ_LABEL = {"daily":"Hàng ngày","weekly":"Hàng tuần","monthly":"Hàng tháng"}

def gen_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ===================== API CƠ BẢN =====================
@app.get("/")
def root():
    return {"message": "API đang chạy!"}

@app.get("/api/config")
def config():
    return {"SUPABASE_URL": os.getenv("SUPABASE_URL"), "SUPABASE_KEY": os.getenv("SUPABASE_KEY")}

# ===================== HOUSEHOLD =====================
@app.post("/api/households")
def create_household(req: CreateHouseRequest):
    code = gen_code()
    res = supabase.table("households").insert([{"name": req.name, "join_code": code}]).execute()
    hid = res.data[0]["id"]
    supabase.table("profiles").update({"household_id": hid, "role": "admin"}).eq("id", req.user_id).execute()
    # Auto-seed tasks cho nhà mới
    defaults = [
        ("Rửa chén",15,2,"daily","Afternoon"),("Đổ rác",5,1,"daily","Evening"),("Nấu cơm",20,3,"daily",None),
        ("Lau nhà",45,8,"weekly","Morning"),("Mua đồ ăn",30,5,"weekly","Morning"),("Nấu ăn",40,7,"weekly","Morning"),
        ("Giặt đồ",20,3,"weekly","Morning"),("Phơi đồ",10,1,"weekly","Afternoon"),("Rửa nhà tắm",60,10,"monthly","Morning"),
    ]
    for t,d,p,f,s in defaults:
        supabase.table("tasks").insert([{"title":t,"duration_minutes":d,"points":p,"frequency":f,"preferred_slot":s,"household_id":hid}]).execute()
    return {"message": "Đã tạo nhà!", "household_id": hid, "join_code": code, "household_name": req.name}

@app.post("/api/households/join")
def join_household(req: JoinHouseRequest):
    house = supabase.table("households").select("*").eq("join_code", req.join_code.strip().upper()).execute().data
    if not house:
        raise HTTPException(404, "Mã gia đình không tồn tại!")
    h = house[0]
    supabase.table("profiles").update({"household_id": h["id"], "role": "member"}).eq("id", req.user_id).execute()
    return {"message": f"Đã tham gia {h['name']}!", "household_id": h["id"], "household_name": h["name"]}

@app.get("/api/households/my")
def my_household(user_id: str):
    data = supabase.table("profiles").select("household_id, households(name, join_code)").eq("id", user_id).single().execute().data
    return data

@app.post("/api/households/leave")
def leave_household(user_id: str):
    supabase.table("profiles").update({"household_id": None, "role": "member"}).eq("id", user_id).execute()
    return {"message": "Đã rời khỏi nhà."}

# ===================== TASK CRUD =====================
@app.get("/tasks")
def get_tasks(household_id: str = ""):
    q = supabase.table("tasks").select("*")
    if household_id:
        q = q.eq("household_id", household_id)
    return {"tasks": q.order("frequency").order("duration_minutes", desc=True).execute().data}

@app.post("/tasks")
def create_task(task: TaskCreate):
    if task.frequency not in ["daily","weekly","monthly"]:
        raise HTTPException(400, "frequency sai")
    if task.preferred_slot and task.preferred_slot not in ["Morning","Afternoon","Evening"]:
        raise HTTPException(400, "preferred_slot sai")
    res = supabase.table("tasks").insert([{
        "title":task.title,"duration_minutes":task.duration_minutes,"points":task.points,
        "frequency":task.frequency,"preferred_slot":task.preferred_slot,"household_id":task.household_id or None
    }]).execute()
    return {"message":"Đã tạo!","task":res.data[0]}

@app.put("/tasks/{task_id}")
def update_task(task_id: str, task: TaskUpdate):
    u = {}
    if task.title is not None: u["title"]=task.title
    if task.duration_minutes is not None: u["duration_minutes"]=task.duration_minutes
    if task.points is not None: u["points"]=task.points
    if task.frequency is not None:
        if task.frequency not in ["daily","weekly","monthly"]: raise HTTPException(400,"frequency sai")
        u["frequency"]=task.frequency
    if task.preferred_slot is not None:
        if task.preferred_slot not in ["Morning","Afternoon","Evening",""]: raise HTTPException(400,"slot sai")
        u["preferred_slot"]=task.preferred_slot if task.preferred_slot else None
    if not u: raise HTTPException(400,"Không có gì cập nhật")
    res = supabase.table("tasks").update(u).eq("id",task_id).execute()
    if not res.data: raise HTTPException(404,"Không tìm thấy")
    return {"message":"Đã cập nhật!","task":res.data[0]}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    res = supabase.table("tasks").delete().eq("id",task_id).execute()
    if not res.data: raise HTTPException(404,"Không tìm thấy")
    return {"message":"Đã xóa!"}

@app.post("/tasks/seed-defaults")
def seed_defaults(household_id: str = ""):
    defaults = [("Rửa chén",15,2,"daily","Afternoon"),("Đổ rác",5,1,"daily","Evening"),("Nấu cơm",20,3,"daily",None),("Lau nhà",45,8,"weekly","Morning"),("Mua đồ ăn",30,5,"weekly","Morning"),("Nấu ăn",40,7,"weekly","Morning"),("Giặt đồ",20,3,"weekly","Morning"),("Phơi đồ",10,1,"weekly","Afternoon"),("Rửa nhà tắm",60,10,"monthly","Morning")]
    inserted = []
    for t,d,p,f,s in defaults:
        q = supabase.table("tasks").select("id").eq("title",t)
        if household_id: q = q.eq("household_id",household_id)
        if not q.execute().data:
            row = {"title":t,"duration_minutes":d,"points":p,"frequency":f,"preferred_slot":s}
            if household_id: row["household_id"]=household_id
            r = supabase.table("tasks").insert([row]).execute()
            if r.data: inserted.append(t)
    return {"message":f"Đã chèn {len(inserted)} việc.","inserted":inserted}

# ===================== SCHEDULER =====================
def smart_scheduler(target_date: str, household_id: str, dry_run: bool = False):
    target = date.fromisoformat(target_date)
    day_name = DAY_MAP[target.weekday()]
    members = supabase.table("profiles").select("id,full_name").eq("role","member").eq("household_id",household_id).execute().data
    if not members:
        return {"success":False,"message":"Không có thành viên trong nhà này!"}
    all_sched = supabase.table("schedules").select("user_id,slot,is_free").eq("day_of_week",day_name).eq("is_free",True).execute().data
    availability = {m["id"]:{"Morning":False,"Afternoon":False,"Evening":False} for m in members}
    for s in all_sched:
        if s["user_id"] in availability and s["slot"] in SLOT_ORDER:
            availability[s["user_id"]][s["slot"]] = True
    active = []
    member_info = {}
    for m in members:
        free = [s for s in SLOT_ORDER if availability[m["id"]][s]]
        if free:
            active.append(m["id"])
            member_info[m["id"]] = {"name":m["full_name"],"free_slots":free,"total_minutes":0,"task_count":0}
    if not active:
        return {"success":False,"message":f"Không ai rảnh vào {day_name}!"}
    q = supabase.table("tasks").select("*")
    q = q.eq("household_id",household_id) if household_id else q
    all_tasks = q.execute().data
    if not all_tasks:
        return {"success":False,"message":"Không có việc nào!"}
    existing_today = supabase.table("assignments").select("task_id,slot,user_id").eq("assigned_date",target_date).execute().data
    existing_ids = set(e["task_id"] for e in existing_today)
    existing_slots = {}
    for e in existing_today:
        sl = e.get("slot","Morning")
        existing_slots[sl] = existing_slots.get(sl,0)+1
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)
    first = target.replace(day=1)
    last = (target.replace(month=target.month+1,day=1)-timedelta(days=1)) if target.month<12 else target.replace(year=target.year+1,month=1,day=1)-timedelta(days=1)
    to_assign = []
    for task in all_tasks:
        if task["id"] in existing_ids: continue
        freq = task.get("frequency","daily")
        if freq == "daily":
            to_assign.append(task)
        elif freq == "weekly":
            w = supabase.table("assignments").select("id").eq("task_id",task["id"]).gte("assigned_date",monday.isoformat()).lte("assigned_date",sunday.isoformat()).execute().data
            if not w: to_assign.append(task)
        elif freq == "monthly":
            m = supabase.table("assignments").select("id").eq("task_id",task["id"]).gte("assigned_date",first.isoformat()).lte("assigned_date",last.isoformat()).execute().data
            if not m: to_assign.append(task)
    if not to_assign:
        return {"success":False,"message":f"Tất cả việc đã được giao cho {target_date}!"}
    to_assign.sort(key=lambda t:(0 if t.get("preferred_slot") else 1,-(t.get("duration_minutes",0))))
    assignments = []
    unassigned = []
    for task in to_assign:
        dur = task.get("duration_minutes",30)
        pref = task.get("preferred_slot")
        best_slot, min_l = None, float('inf')
        for slot in SLOT_ORDER:
            ppl = [uid for uid in active if availability[uid][slot]]
            if not ppl: continue
            if pref and slot == pref: best_slot=slot; break
            if existing_slots.get(slot,0) < min_l: min_l=existing_slots.get(slot,0); best_slot=slot
        if not best_slot: unassigned.append({"task":task["title"],"reason":"Không có ca"}); continue
        cands = [uid for uid in active if availability[uid][best_slot]]
        if not cands: unassigned.append({"task":task["title"],"reason":"Không ai rảnh"}); continue
        best = min(cands, key=lambda uid:member_info[uid]["total_minutes"])
        assignments.append({"task_id":task["id"],"user_id":best,"assigned_date":target_date,"status":"pending","slot":best_slot})
        member_info[best]["total_minutes"]+=dur
        member_info[best]["task_count"]+=1
        existing_slots[best_slot]=existing_slots.get(best_slot,0)+1
    result = []
    if assignments and not dry_run:
        res = supabase.table("assignments").insert(assignments).execute()
        result = res.data
        for a in result:
            td = next((t for t in all_tasks if t["id"]==a["task_id"]),{})
            a["task_title"]=td.get("title","?")
            a["task_points"]=td.get("points",0)
            a["assigned_to"]=member_info.get(a["user_id"],{}).get("name","?")
    if not dry_run:
        try: supabase.table("schedule_logs").insert([{"scheduled_date":target_date,"assignments_count":len(assignments),"detail":{"day":day_name,"member_info":member_info,"unassigned":unassigned}}]).execute()
        except: pass
    return {"success":len(assignments)>0,"message":f"Đã phân {len(assignments)} việc." if assignments else "Không có việc mới.","date":target_date,"day":day_name,"is_preview":dry_run,"assignments":result,"summary":member_info,"unassigned":unassigned,"slots_used":existing_slots,"already_existed":len(existing_ids)}

@app.post("/preview-algorithm")
def preview(req: AssignRequest):
    try: return smart_scheduler(req.date, req.household_id, dry_run=True)
    except Exception as e: raise HTTPException(500,str(e))

@app.post("/run-algorithm")
def run(req: AssignRequest):
    try: return smart_scheduler(req.date, req.household_id, dry_run=False)
    except Exception as e: raise HTTPException(500,str(e))

@app.get("/weekly-schedule")
def weekly(household_id: str = ""):
    try:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        # Lấy member IDs của nhà này
        members = supabase.table("profiles").select("id").eq("household_id",household_id).execute().data if household_id else []
        mids = [m["id"] for m in members]
        results = {}
        for i in range(7):
            d = monday+timedelta(days=i); ds = d.isoformat()
            q = supabase.table("assignments").select("id,status,slot,tasks(title,points,duration_minutes,frequency),profiles(full_name)").eq("assigned_date",ds)
            if mids: q = q.in_("user_id",mids)
            results[ds] = {"day":DAY_MAP[d.weekday()],"date_label":d.strftime("%d/%m"),"is_today":d==today,"assignments":q.execute().data or []}
        return {"week_start":monday.isoformat(),"days":results}
    except Exception as e: raise HTTPException(500,str(e))

@app.delete("/clear-schedule/{target_date}")
def clear(target_date: str):
    res = supabase.table("assignments").delete().eq("assigned_date",target_date).execute()
    return {"message":f"Đã xóa {len(res.data)} việc."}

# ===================== DUYỆT & ĐIỂM =====================
@app.post("/approve-task/{assignment_id}")
def approve(assignment_id: str, req: ApproveRequest):
    try:
        supabase.table("assignments").update({"status":"completed"}).eq("id",assignment_id).execute()
        a = supabase.table("assignments").select("user_id").eq("id",assignment_id).single().execute()
        p = supabase.table("profiles").select("total_points").eq("id",a.data["user_id"]).single().execute()
        cur = p.data.get("total_points",0)
        supabase.table("profiles").update({"total_points":cur+req.points_to_add}).eq("id",a.data["user_id"]).execute()
        return {"message":"Đã duyệt!"}
    except Exception as e: raise HTTPException(500,str(e))

# ===================== CHI TIÊU =====================
@app.get("/calculate-settlement")
def settlement(household_id: str = ""):
    q = supabase.table("expenses").select("*").eq("is_settled",False)
    uq = supabase.table("profiles").select("id,full_name").eq("role","member")
    if household_id: uq = uq.eq("household_id",household_id)
    expenses = q.execute().data
    users = uq.execute().data
    n = len(users)
    if n==0: return {"total":0,"each_must_pay":0,"summary":{}}
    total = sum(float(e["amount"]) for e in expenses)
    fair = total/n
    bal = {u["id"]:{"name":u["full_name"],"spent":0,"net":0} for u in users}
    for e in expenses:
        if e["payer_id"] in bal: bal[e["payer_id"]]["spent"]+=float(e["amount"])
    for uid in bal: bal[uid]["net"]=bal[uid]["spent"]-fair
    return {"total":total,"each_must_pay":fair,"summary":bal}

@app.post("/settle-month")
def settle(household_id: str = ""):
    try:
        q = supabase.table("expenses").select("*").eq("is_settled",False)
        uq = supabase.table("profiles").select("id,full_name").eq("role","member")
        if household_id: uq = uq.eq("household_id",household_id)
        expenses = q.execute().data; users = uq.execute().data; n=len(users)
        if n==0 or not expenses: return {"message":"Không có dữ liệu."}
        total = sum(float(e["amount"]) for e in expenses); fair=total/n
        summary = {}
        for u in users:
            spent = sum(float(e["amount"]) for e in expenses if e["payer_id"]==u["id"])
            summary[u["full_name"]]={"chi":spent,"can_tra":fair,"cong_no":round(spent-fair,2)}
        supabase.table("expenses").update({"is_settled":True}).eq("is_settled",False).execute()
        try: supabase.table("settlement_history").insert([{"total":total,"per_person":fair,"summary":summary}]).execute()
        except: pass
        return {"message":"Chốt sổ thành công!","total":total,"per_person":fair}
    except Exception as e: raise HTTPException(500,str(e))