const state = {
  role: localStorage.getItem("role"),
  token: localStorage.getItem("token"),
  student: JSON.parse(localStorage.getItem("student") || "null"),
  teacherTab: "dashboard",
  examData: null,
  answers: {},
  currentQuestion: 0,
  saveTimer: null,
  monitorTimer: null,
};

const $app = document.querySelector("#app");

function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return fetch(path, { ...options, headers }).then(async (res) => {
    if (path.includes("/export")) return res;
    const body = await res.json();
    if (!res.ok || body.code !== 200) throw new Error(body.message || "请求失败");
    return body.data;
  });
}

function html(strings, ...values) {
  return strings.reduce((out, s, i) => out + s + (values[i] ?? ""), "");
}

function setSession(role, token, student = null) {
  state.role = role;
  state.token = token;
  state.student = student;
  localStorage.setItem("role", role);
  localStorage.setItem("token", token);
  if (student) localStorage.setItem("student", JSON.stringify(student));
}

function logout() {
  localStorage.clear();
  Object.assign(state, { role: null, token: null, student: null, examData: null, answers: {} });
  clearInterval(state.saveTimer);
  clearInterval(state.monitorTimer);
  renderHome();
}

function renderHome() {
  if (state.role === "teacher" && state.token) return renderTeacher();
  if (state.role === "student" && state.token) return renderStudentGate();
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="login-box">
        <h1>局域网考试系统</h1>
        <div class="tabs">
          <button onclick="renderStudentLogin()">学生登录</button>
          <button class="secondary" onclick="renderTeacherLogin()">教师登录</button>
        </div>
        <p class="hint">学生账号为学号，密码为学号后 5 位。教师默认账号 admin / admin123。</p>
      </div>
    </section>
  `;
}

function renderStudentLogin() {
  $app.innerHTML = html`
    <section class="login-wrap">
      <form class="login-box" onsubmit="studentLogin(event)">
        <h1>学生登录</h1>
        <label class="field">学号<input name="student_id" placeholder="请输入学号" required /></label>
        <label class="field">密码<input name="password" type="password" placeholder="学号后 5 位" required /></label>
        <button type="submit">登录</button>
        <button type="button" class="secondary" onclick="renderHome()">返回</button>
        <div id="msg" class="msg"></div>
      </form>
    </section>
  `;
}

async function studentLogin(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("/api/student/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form)),
    });
    setSession("student", data.token, data.student);
    renderStudentGate();
  } catch (err) {
    document.querySelector("#msg").textContent = err.message;
  }
}

function renderTeacherLogin() {
  $app.innerHTML = html`
    <section class="login-wrap">
      <form class="login-box" onsubmit="teacherLogin(event)">
        <h1>教师登录</h1>
        <label class="field">账号<input name="username" value="admin" required /></label>
        <label class="field">密码<input name="password" type="password" value="admin123" required /></label>
        <button type="submit">登录</button>
        <button type="button" class="secondary" onclick="renderHome()">返回</button>
        <div id="msg" class="msg"></div>
      </form>
    </section>
  `;
}

async function teacherLogin(event) {
  event.preventDefault();
  try {
    const data = await api("/api/teacher/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.target))),
    });
    setSession("teacher", data.token);
    renderTeacher();
  } catch (err) {
    document.querySelector("#msg").textContent = err.message;
  }
}

function renderStudentGate() {
  $app.innerHTML = html`
    <section class="login-wrap">
      <form class="login-box" onsubmit="confirmName(event)">
        <h1>姓名确认</h1>
        <p>学号：${state.student.student_id}</p>
        <label class="field">真实姓名<input name="name" placeholder="请输入名单中的姓名" required /></label>
        <button type="submit">确认身份</button>
        <button type="button" class="secondary" onclick="logout()">退出</button>
        <div id="msg" class="msg"></div>
      </form>
    </section>
  `;
}

async function confirmName(event) {
  event.preventDefault();
  try {
    await api("/api/student/confirm-name", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.target))),
    });
    renderInstructions();
  } catch (err) {
    document.querySelector("#msg").textContent = err.message;
  }
}

async function renderInstructions() {
  const data = await api("/api/exam/current");
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="login-box">
        <h1>考试须知</h1>
        <p><strong>${data.exam.name}</strong></p>
        <p>试卷：${data.paper.title}</p>
        <p>时长：${data.exam.duration_minutes} 分钟，总分：${data.paper.total_score} 分</p>
        <p class="hint">进入答题后会每 30 秒自动保存。关闭浏览器后重新登录，可恢复已保存答案。</p>
        <label class="option"><input id="agree" type="checkbox" /> 我已阅读考试须知</label>
        <button onclick="startStudentExam()">开始答题</button>
        <button class="secondary" onclick="logout()">退出</button>
        <div id="msg" class="msg"></div>
      </div>
    </section>
  `;
}

async function startStudentExam() {
  if (!document.querySelector("#agree").checked) {
    document.querySelector("#msg").textContent = "请先勾选已阅读考试须知";
    return;
  }
  const data = await api("/api/exam/paper");
  state.examData = data;
  state.answers = data.record.answers || {};
  renderExam();
  state.saveTimer = setInterval(autoSave, 30000);
  window.addEventListener("beforeunload", autoSave);
}

function isAnswered(question) {
  return String(state.answers[question.question_id] || "").trim() !== "";
}

function answeredCount() {
  return state.examData.questions.filter(isAnswered).length;
}

function renderExam() {
  const { exam, questions, record } = state.examData;
  const start = record.start_time ? new Date(record.start_time).getTime() : Date.now();
  const end = start + exam.duration_minutes * 60 * 1000;
  const percent = Math.round((answeredCount() / questions.length) * 100);
  $app.innerHTML = html`
    <div class="shell">
      <div class="exam-top">
        <strong>${exam.name}</strong>
        <span id="timer" class="timer">--:--:--</span>
        <button class="danger" onclick="submitExam(false)">交卷</button>
      </div>
      <div class="exam-layout">
        <aside class="panel question-nav">
          <h3>题目导航</h3>
          <div class="q-buttons">
            ${questions
              .map(
                (q, i) =>
                  `<button class="qbtn ${isAnswered(q) ? "done" : ""} ${i === state.currentQuestion ? "current" : ""}" onclick="jumpQuestion(${i})">${q.question_no}</button>`
              )
              .join("")}
          </div>
          <div class="progress"><span style="width:${percent}%"></span></div>
          <p>已答 ${answeredCount()} / 共 ${questions.length} 题</p>
        </aside>
        <main>
          ${questions.map((q, i) => renderQuestion(q, i)).join("")}
        </main>
      </div>
    </div>
  `;
  updateTimer(end);
  state.countdown = setInterval(() => updateTimer(end), 1000);
}

function renderQuestion(q, index) {
  const label = { single_choice: "单选题", multiple_choice: "多选题", true_false: "判断题", short_answer: "简答题" }[q.type];
  return html`
    <section id="q${index}" class="panel question">
      <h3>第 ${q.question_no} 题（${label}，${q.score} 分）</h3>
      <p>${q.content}</p>
      ${renderAnswerControl(q)}
      <div class="actions">
        <button class="secondary" onclick="jumpQuestion(${Math.max(0, index - 1)})">上一题</button>
        <button class="secondary" onclick="jumpQuestion(${Math.min(state.examData.questions.length - 1, index + 1)})">下一题</button>
      </div>
    </section>
  `;
}

function renderAnswerControl(q) {
  const current = state.answers[q.question_id] || "";
  if (q.type === "short_answer") {
    return `<textarea oninput="setAnswer('${q.question_id}', this.value)">${current}</textarea>`;
  }
  if (q.type === "true_false") {
    return `
      <label class="option"><input type="radio" name="q${q.question_id}" ${current === "true" ? "checked" : ""} onchange="setAnswer('${q.question_id}', 'true')" /> 正确</label>
      <label class="option"><input type="radio" name="q${q.question_id}" ${current === "false" ? "checked" : ""} onchange="setAnswer('${q.question_id}', 'false')" /> 错误</label>
    `;
  }
  const multiple = q.type === "multiple_choice";
  const selected = current ? current.split(",") : [];
  return q.options
    .map(
      (opt) => `
      <label class="option">
        <input type="${multiple ? "checkbox" : "radio"}" name="q${q.question_id}" value="${opt.key}" ${selected.includes(opt.key) ? "checked" : ""} onchange="setChoice('${q.question_id}', '${q.type}')" />
        <span>${opt.key}. ${opt.value}</span>
      </label>`
    )
    .join("");
}

function setAnswer(questionId, value) {
  state.answers[questionId] = value;
  refreshNavOnly();
}

function setChoice(questionId, type) {
  const inputs = [...document.querySelectorAll(`[name="q${questionId}"]:checked`)];
  state.answers[questionId] = type === "multiple_choice" ? inputs.map((i) => i.value).sort().join(",") : inputs[0]?.value || "";
  refreshNavOnly();
}

function refreshNavOnly() {
  document.querySelectorAll(".qbtn").forEach((btn, i) => {
    btn.classList.toggle("done", isAnswered(state.examData.questions[i]));
  });
  const percent = Math.round((answeredCount() / state.examData.questions.length) * 100);
  document.querySelector(".progress span").style.width = `${percent}%`;
  document.querySelector(".question-nav p").textContent = `已答 ${answeredCount()} / 共 ${state.examData.questions.length} 题`;
}

function jumpQuestion(index) {
  state.currentQuestion = index;
  document.querySelector(`#q${index}`).scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll(".qbtn").forEach((btn, i) => btn.classList.toggle("current", i === index));
}

function updateTimer(end) {
  const left = Math.max(0, end - Date.now());
  const total = Math.floor(left / 1000);
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  const timer = document.querySelector("#timer");
  if (!timer) return;
  timer.textContent = `${h}:${m}:${s}`;
  timer.classList.toggle("danger-time", total <= 300);
  if (left <= 0) submitExam(true);
}

async function autoSave() {
  if (!state.examData) return;
  await api("/api/exam/auto-save", { method: "POST", body: JSON.stringify({ answers: state.answers }) }).catch(() => {});
}

async function submitExam(auto) {
  if (!auto) {
    const left = state.examData.questions.length - answeredCount();
    if (!confirm(`还有 ${left} 题未答，确认交卷吗？`)) return;
  }
  clearInterval(state.saveTimer);
  await api("/api/exam/submit", { method: "POST", body: JSON.stringify({ answers: state.answers }) });
  $app.innerHTML = `<section class="login-wrap"><div class="login-box"><h1>提交成功</h1><p>你的答卷已保存并提交。</p><button onclick="logout()">退出系统</button></div></section>`;
}

function renderTeacher() {
  clearInterval(state.monitorTimer);
  const tabs = [
    ["dashboard", "仪表盘"],
    ["students", "学生管理"],
    ["papers", "试卷管理"],
    ["exam", "考试管理"],
    ["monitor", "监控面板"],
    ["grading", "批改评分"],
    ["results", "成绩管理"],
  ];
  $app.innerHTML = html`
    <div class="shell">
      <header class="topbar"><span class="brand">局域网考试系统教师端</span><button class="secondary" onclick="logout()">退出登录</button></header>
      <div class="layout">
        <aside class="sidebar">${tabs.map(([id, name]) => `<button class="sidebtn ${state.teacherTab === id ? "active" : ""}" onclick="setTeacherTab('${id}')">${name}</button>`).join("")}</aside>
        <main id="teacher-main" class="main"></main>
      </div>
    </div>
  `;
  renderTeacherTab();
}

function setTeacherTab(tab) {
  state.teacherTab = tab;
  renderTeacher();
}

async function renderTeacherTab() {
  const main = document.querySelector("#teacher-main");
  if (state.teacherTab === "dashboard") return renderDashboard(main);
  if (state.teacherTab === "students") return renderStudents(main);
  if (state.teacherTab === "papers") return renderPapers(main);
  if (state.teacherTab === "exam") return renderExamAdmin(main);
  if (state.teacherTab === "monitor") return renderMonitor(main);
  if (state.teacherTab === "grading") return renderGrading(main);
  if (state.teacherTab === "results") return renderResults(main);
}

async function renderDashboard(main) {
  const [exam, monitor] = await Promise.all([api("/api/exam/current"), api("/api/teacher/monitor")]);
  main.innerHTML = html`
    <section class="panel"><h2>系统概览</h2><p>${exam.exam.name}：${exam.exam.status}</p></section>
    <section class="grid">
      <div class="metric">学生总数<strong>${monitor.stats.total}</strong></div>
      <div class="metric">已登录<strong>${monitor.stats.logged_in}</strong></div>
      <div class="metric">答题中<strong>${monitor.stats.answering}</strong></div>
      <div class="metric">已提交<strong>${monitor.stats.submitted}</strong></div>
    </section>
  `;
}

async function renderStudents(main) {
  const rows = await api("/api/students");
  main.innerHTML = `<section class="panel"><h2>学生管理</h2><p>已内置学生 ${rows.length} 人。账号为学号，密码为学号后 5 位。</p>${table(["学号", "姓名", "班级"], rows.map((r) => [r.student_id, r.name, r.class_name]))}</section>`;
}

async function renderPapers(main) {
  const rows = await api("/api/papers");
  main.innerHTML = `<section class="panel"><h2>试卷管理</h2>${table(["试卷编号", "标题", "总分", "时长"], rows.map((r) => [r.paper_id, r.title, r.total_score, `${r.duration_minutes} 分钟`]))}</section>`;
}

async function renderExamAdmin(main) {
  const data = await api("/api/exam/current");
  main.innerHTML = html`
    <section class="panel">
      <h2>考试管理</h2>
      <p>${data.exam.name}</p>
      <p>当前状态：<span class="status ${data.exam.status}">${data.exam.status}</span></p>
      <div class="actions">
        <button onclick="adminAction('/api/teacher/start-exam')">开始考试</button>
        <button class="danger" onclick="adminAction('/api/teacher/end-exam')">结束考试</button>
      </div>
    </section>
  `;
}

async function adminAction(path) {
  await api(path, { method: "POST", body: JSON.stringify({}) });
  renderTeacher();
}

async function renderMonitor(main) {
  const data = await api("/api/teacher/monitor");
  main.innerHTML = html`
    <section class="grid">
      <div class="metric">总人数<strong>${data.stats.total}</strong></div>
      <div class="metric">已登录<strong>${data.stats.logged_in}</strong></div>
      <div class="metric">答题中<strong>${data.stats.answering}</strong></div>
      <div class="metric">已提交<strong>${data.stats.submitted}</strong></div>
      <div class="metric">未登录<strong>${data.stats.not_logged_in}</strong></div>
    </section>
    <section class="panel">
      <h2>监控面板</h2>
      ${table(["学号", "姓名", "状态", "进度", "操作"], data.students.map((s) => [
        s.student_id,
        s.name,
        `<span class="status ${s.status}">${statusName(s.status)}</span>`,
        `${s.answered} / ${s.total}`,
        s.submitted ? "" : `<button class="danger" onclick="forceSubmit('${s.student_id}')">强制收卷</button>`,
      ]))}
    </section>
  `;
  state.monitorTimer = setTimeout(() => renderMonitor(main), 5000);
}

async function forceSubmit(studentId) {
  await api("/api/teacher/force-submit", { method: "POST", body: JSON.stringify({ student_id: studentId }) });
  renderTeacher();
}

async function renderGrading(main) {
  const rows = await api("/api/grading/records");
  main.innerHTML = html`
    <section class="panel"><h2>批改评分</h2>
      ${rows.length ? rows.map((r) => `
        <div class="panel">
          <strong>${r.student_id} ${r.name} - 第 ${r.question_no} 题</strong>
          <p>${r.content}</p>
          <p>学生答案：${r.answer_text || "未作答"}</p>
          <div class="actions">
            <input style="max-width:140px" id="score${r.answer_id}" type="number" min="0" max="${r.max_score}" value="${r.score || 0}" />
            <button onclick="saveScore(${r.answer_id})">保存评分</button>
          </div>
        </div>`).join("") : "<p>暂无可批改的简答题答案。</p>"}
    </section>
  `;
}

async function saveScore(answerId) {
  const score = document.querySelector(`#score${answerId}`).value;
  await api("/api/grading/manual", { method: "POST", body: JSON.stringify({ answer_id: answerId, score }) });
  alert("评分已保存");
}

async function renderResults(main) {
  const data = await api("/api/teacher/results");
  main.innerHTML = html`
    <section class="grid">
      <div class="metric">平均分<strong>${data.stats.average}</strong></div>
      <div class="metric">最高分<strong>${data.stats.highest}</strong></div>
      <div class="metric">最低分<strong>${data.stats.lowest}</strong></div>
      <div class="metric">及格率<strong>${data.stats.pass_rate}%</strong></div>
    </section>
    <section class="panel">
      <div class="actions"><h2>成绩管理</h2><button onclick="downloadResults()">导出成绩</button></div>
      ${table(["学号", "姓名", "客观分", "主观分", "总分", "状态"], data.results.map((r) => [r.student_id, r.name, r.objective_score, r.subjective_score, r.total_score, statusName(r.status || "not_logged_in")]))}
    </section>
  `;
}

async function downloadResults() {
  const res = await api("/api/teacher/export");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "exam-results.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function table(headers, rows) {
  return `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c ?? ""}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function statusName(status) {
  return {
    not_logged_in: "未登录",
    logged_in: "已登录",
    confirmed: "已确认",
    answering: "答题中",
    submitted: "已提交",
    forced: "已强制收卷",
    waiting: "未开始",
    running: "进行中",
    ended: "已结束",
  }[status] || status;
}

renderHome();
