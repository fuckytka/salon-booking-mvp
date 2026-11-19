"use strict";
const $ = id => document.getElementById(id);
const view = $("view"), notice = $("notice"), alertBox = $("error");
const state = {user:null, service:null, slot:null, slots:[], day:null, request:null, page:"new", busy:false};
const messages = {
  invalid_data:"Перевірте введені дані.", not_found:"Цей запис або час уже недоступний.",
  slot_unavailable:"На обраний час уже немає вільного місця. Оберіть інший час.",
  slot_occupied:"У цей час уже є записи. Блокування неможливе.",
  account_exists:"Для цієї пошти вже є профіль. Увійдіть у нього.",
  invalid_credentials:"Перевірте електронну пошту й пароль.",
  login_required:"Сеанс завершився. Увійдіть знову.",
  forbidden:"Ця дія недоступна для вашої ролі.", request_conflict:"Запит змінився. Оберіть час повторно.",
  offline:"Немає зв’язку із салоном. Перевірте з’єднання та повторіть дію.",
  storage_error:"Сервіс тимчасово недоступний. Спробуйте ще раз."
};
const fields = {email:"Введіть адресу у форматі name@example.com.",password:"Введіть пароль довжиною від 8 до 128 символів.",full_name:"Введіть ім’я довжиною від 2 до 80 символів."};
const escape = text => String(text).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
function fmt(value,options) { return new Intl.DateTimeFormat("uk-UA",{timeZone:"Europe/Kyiv",...options}).format(new Date(value)); }
const day = at => fmt(at,{weekday:"long",day:"numeric",month:"long"});
const hour = at => fmt(at,{hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
const times = s => hour(s.start_at)+" – "+hour(s.end_at);
async function api(path,method="GET",body) {
  let response;
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),10000);
  try {
    response = await fetch("/api/v1"+path,{method,signal:controller.signal,headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(),result.error);
    return result;
  } catch (e) { if(e.code && typeof e.code==="string") throw e; throw {code:"offline"}; }
  finally {clearTimeout(timer);}
}
function nav() {
  $("logout").hidden = !state.user;
  $("schedule").hidden = !state.user || state.user.role==="customer";
  for(const id of ["new","mine","schedule"]) {
    if(id===state.page) $(id).setAttribute("aria-current","page");
    else $(id).removeAttribute("aria-current");
  }
}
async function run(action) {
  if(state.busy) return;
  state.busy=true;
  alertBox.replaceChildren();
  notice.textContent="Завантаження…";
  view.setAttribute("aria-busy","true");
  try {
    const message = await action();
    notice.textContent = typeof message==="string" ? message : "";
  } catch(e) {
    notice.textContent="";
    const msg = fields[e.field] || messages[e.code] || messages.storage_error;
    alertBox.textContent = msg;
    const input = e.field && $(e.field);
    if(input && input.tagName==="INPUT"){
      input.setAttribute("aria-invalid","true");
      $("err-"+e.field).textContent=msg;
      input.focus();
    } else {
      const retry=document.createElement("button");
      retry.textContent = e.code==="slot_unavailable"?"Обрати інший час":e.code==="login_required"?"Увійти":"Повторити";
      retry.onclick=()=>run(e.code==="slot_unavailable"?loadSlots:e.code==="login_required"?()=>auth(false,()=>catalog()):action);
      alertBox.append(" ",retry);
      alertBox.focus();
    }
  } finally { state.busy=false;view.removeAttribute("aria-busy");nav(); }
}
function screen(title,kicker,description,html) {
  view.innerHTML='<p class="kicker">'+kicker+'</p><h1 id="title" tabindex="-1">'+title+'</h1><p class="lead">'+description+'</p>'+html;
  $("title").focus();
}
function button(id,label,fn) {$(id).onclick=()=>run(fn);}
function summary() {
  const s=state.service;
  return '<aside class="panel summary"><h2>Ваш вибір</h2><p><strong>'+escape(s.title)+'</strong></p><p>'+s.duration+' хв · '+s.price+' грн</p>'+
    (state.slot?'<p>'+escape(day(state.slot.start_at))+'</p><p>'+times(state.slot)+'</p>':'')+'</aside>';
}
async function catalog() {
  const services=await api("/services");
  Object.assign(state,{page:"new",service:null,slot:null,request:null,day:null});
  screen("Запис до салону","Онлайн-бронювання","Оберіть послугу, щоб побачити доступні дні й години.",
    '<div class="grid">'+services.map((s,i)=>'<button class="service" id="service-'+i+'"><strong>'+escape(s.title)+'</strong><small>'+s.duration+' хв</small><span class="price">'+s.price+' грн</span></button>').join("")+'</div>'+
    (!services.length?'<p>Наразі немає доступних послуг.</p>':'')+'<p class="tagline">Переглянути або скасувати свій запис можна в розділі «Мої записи».</p>');
  services.forEach((s,i)=>button("service-"+i,s.title,()=>{state.service=s;state.day=null;return loadSlots();}));
}
async function loadSlots() {
  const result=await api("/slots?service_id="+encodeURIComponent(state.service.service_id));
  state.slots=result;state.slot=null;state.request=null;
  renderSlots();
}
function renderSlots() {
  const result=state.slots;
  const dates=[...new Set(result.map(s=>day(s.start_at)))];
  if(!dates.includes(state.day)) state.day=dates[0];
  const rows=result.filter(s=>day(s.start_at)===state.day);
  screen("Вільний час","Запис до салону","Оберіть день і час початку візиту.",
    '<div class="layout"><div class="panel">'+(dates.length?'<label for="day">День візиту</label><select id="day">'+dates.map(d=>'<option'+(state.day===d?' selected':'')+'>'+escape(d)+'</option>').join("")+'</select><h2>Години запису</h2><div class="slotgrid">'+rows.map((s,i)=>'<button id="slot-'+i+'"><strong>'+times(s)+'</strong><small>Вільних місць: '+s.remaining_capacity+' із 3</small></button>').join("")+'</div>':'<p>Доступного часу для цієї послуги поки немає.</p>')+'<button id="back" class="link">Змінити послугу</button></div>'+summary()+'</div>');
  if ($("day")) $("day").onchange=()=>{state.day=$("day").value;renderSlots();$("day").focus();};
  rows.forEach((s,i)=>button("slot-"+i,"",()=>{state.slot=s;state.request=crypto.randomUUID();return state.user?confirmView():auth(true,confirmView);}));
  button("back","",catalog);
}
function auth(register,next) {
  const makeField=(id,label,type,complete)=>'<label for="'+id+'">'+label+'</label><input id="'+id+'" name="'+id+'" type="'+type+'" autocomplete="'+complete+'" required aria-describedby="err-'+id+'"><div class="field-error" id="err-'+id+'"></div>';
  screen(register?"Дані клієнта":"Вхід до профілю",state.slot?"Запис до салону":"Особистий профіль",
    register?"Створіть профіль, щоб керувати своїми записами.":"Увійдіть, використовуючи свою пошту й пароль.",
    '<div class="layout"><form id="auth" class="panel" novalidate>'+
    (register?makeField("full_name","Ім’я","text","name"):"")+makeField("email","Електронна пошта","email","email")+
    makeField("password",register?"Пароль (8–128 символів)":"Пароль","password",register?"new-password":"current-password")+
    '<div class="actions"><button class="primary">'+(register?"Створити профіль":"Увійти")+'</button><button id="switch" class="link" type="button">'+(register?"Уже маю профіль":"Створити профіль")+'</button></div></form>'+(state.slot?summary():"")+'</div>');
  button("switch","",()=>auth(!register,next));
  $("auth").onsubmit=event=>{
    event.preventDefault();
    run(async()=>{
      for(const el of $("auth").querySelectorAll("input")){el.removeAttribute("aria-invalid");$("err-"+el.id).textContent="";}
      const data=Object.fromEntries(new FormData($("auth")));
      const {user}=await api(register?"/register":"/login","POST",data);
      state.user=user;
      return user.role==="customer"?next():staff();
    });
  };
}
function confirmView() {
  screen("Підтвердження візиту","Запис до салону","Перевірте послугу та час перед записом.",
    '<div class="layout"><div class="panel"><h2>'+escape(state.user.full_name)+'</h2><p>Для вас буде зарезервовано одне місце. Після підтвердження запис з’явиться у вашому профілі.</p><div class="actions"><button class="primary" id="confirm">Підтвердити запис</button><button id="change" class="link">Змінити час</button></div></div>'+summary()+'</div>');
  button("confirm","",async()=>{const b=await api("/bookings","POST",{request_id:state.request,service_id:state.service.service_id,timeslot_id:state.slot.timeslot_id});
    showDetail(b);return "Запис створено.";});
  button("change","",loadSlots);
}
function showDetail(b) {
  state.page="mine";
  screen("Ваш візит","Мої записи",escape(day(b.start_at)),
    '<div class="layout"><article class="panel"><h2>'+escape(b.service_title)+'</h2><p>'+times(b)+'</p><p class="status">'+(b.status==="confirmed"?"Підтверджено":"Скасовано")+'</p><div class="actions"><button id="all">Усі мої записи</button>'+(b.status==="confirmed"?'<button class="link" id="cancel">Скасувати запис</button>':'')+'</div><div id="decision"></div></article></div>');
  button("all","",mine);
  if($("cancel")) button("cancel","",()=>{
    $("decision").innerHTML='<p>Скасувати цей візит і звільнити місце?</p><div class="actions"><button class="primary" id="keep">Залишити запис</button><button id="yes">Так, скасувати</button></div>';
    $("keep").focus();button("keep","",()=>showDetail(b));
    button("yes","",async()=>{showDetail(await api("/bookings/"+b.booking_id,"PATCH",{status:"cancelled"}));return "Запис скасовано. Місце знову доступне.";});
  });
}
async function mine() {
  state.page="mine";state.slot=null;
  if(!state.user)return auth(false,mine);
  if(state.user.role!=="customer")return staff();
  const rows=await api("/bookings");
  screen("Мої записи","Особистий профіль",escape(state.user.full_name),
    '<section class="panel">'+(rows.length?rows.map((b,i)=>'<div class="row"><div><strong>'+escape(b.service_title)+'</strong><span>'+escape(day(b.start_at))+' · '+times(b)+'</span><small>'+(b.status==="confirmed"?"Підтверджено":"Скасовано")+'</small></div><button id="detail-'+i+'" aria-label="Відкрити запис '+escape(b.service_title)+' '+escape(day(b.start_at))+' '+hour(b.start_at)+'">Відкрити</button></div>').join(""):'<h2>У вас ще немає записів</h2><p>Оберіть послугу, щоб запланувати перший візит.</p><button id="first" class="primary">Обрати послугу</button>')+'</section>');
  rows.forEach((b,i)=>button("detail-"+i,"",async()=>showDetail(await api("/bookings/"+b.booking_id))));
  if($("first"))button("first","",catalog);
}
async function staff() {
  const rows=await api("/schedule");state.page="schedule";
  screen("Розклад салону","Кабінет працівника","Блокувати можна лише час без підтверджених записів.",
    '<div class="panel">'+rows.map((s,i)=>'<div class="row"><div><strong>'+escape(day(s.start_at))+' · '+times(s)+'</strong><span>Зайнято '+s.active_count+' із 3 · '+(s.is_blocked?"Заблоковано":"Відкрито")+'</span></div><button id="manage-'+i+'">'+(s.is_blocked?"Відкрити":"Заблокувати")+'</button></div>').join("")+'</div>');
  rows.forEach((s,i)=>button("manage-"+i,"",async()=>{await api("/schedule/"+s.timeslot_id,"PATCH",{is_blocked:!s.is_blocked});await staff();return "Розклад оновлено."; }));
}
button("new","",catalog);button("mine","",mine);button("schedule","",staff);
document.querySelector(".brand").onclick=e=>{e.preventDefault();run(catalog)};
button("logout","",async()=>{await api("/logout","POST",{});state.user=null;await catalog();return "Ви вийшли з профілю.";});
run(async()=>{state.user=(await api("/session")).user;return state.user && state.user.role!=="customer"?staff():catalog();});
