/* ===== 全局 JavaScript 工具函数 ===== */

const defaultCategories = ['餐饮', '交通', '日用百货', '购物', '娱乐', '学习', '住房', '医疗健康', '旅行', '通讯', '转账', '退款', '投资', '其他'];
window.categoryOptions = [...defaultCategories];

async function loadCustomCategories() {
    try {
        const res = await fetch('/api/category-options');
        const custom = await res.json();
        const labels = custom.map(c => {
            const parent = c.parent_id ? custom.find(p => p.id === c.parent_id) : null;
            return parent ? `${parent.name}/${c.name}` : c.name;
        });
        window.categoryOptions = [...new Set([...defaultCategories, ...labels])];
    } catch (err) {
        console.warn('加载自定义分类失败:', err);
    }
}

async function createCategoryFromSelect(select) {
    const value = await showPromptModal('新建分类', '输入分类名称，如：餐饮 或 餐饮/咖啡');
    if (!value || !value.trim()) {
        select.value = '';
        return;
    }
    const parts = value.trim().split('/').map(s => s.trim()).filter(Boolean);
    let parentId = null;
    let label = parts[0];
    try {
        const existing = await fetch('/api/category-options').then(r => r.json());
        if (parts.length > 1) {
            const parent = existing.find(c => !c.parent_id && c.name === parts[0]);
            if (!parent) {
                const createdParent = await jsonRequest('/api/categories', 'POST', {name: parts[0]});
                parentId = createdParent.id;
            } else {
                parentId = parent.id;
            }
            label = `${parts[0]}/${parts[1]}`;
            await jsonRequest('/api/categories', 'POST', {name: parts[1], parent_id: parentId});
        } else {
            await jsonRequest('/api/categories', 'POST', {name: parts[0]});
        }
        window.categoryOptions = [...new Set([...window.categoryOptions, label])];
        const option = new Option(label, label, true, true);
        select.add(option);
        select.value = label;
    } catch (err) {
        showAlert('新建分类失败: ' + err.message, 'error');
        select.value = '';
    }
}

loadCustomCategories();

/**
 * 自定义输入弹窗，替代浏览器 prompt()
 * @param {string} title 弹窗标题
 * @param {string} placeholder 输入框占位文字
 * @param {string} defaultValue 默认值
 * @returns {Promise<string|null>} 用户输入的内容，取消返回 null
 */
function showPromptModal(title, placeholder, defaultValue = '') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);z-index:2000;display:flex;align-items:center;justify-content:center;';
        overlay.innerHTML = `
            <div class="card" style="max-width:450px;width:90%;">
                <h3 style="margin-bottom:12px;">${title}</h3>
                <input type="text" id="modal-input" placeholder="${placeholder}" value="${defaultValue}" style="width:100%;padding:10px 12px;font-size:15px;border:1px solid #cbd5e0;border-radius:6px;margin-bottom:14px;box-sizing:border-box;">
                <div style="display:flex;gap:8px;justify-content:flex-end;">
                    <button class="btn" id="modal-cancel" style="background:#a0aec0;">取消</button>
                    <button class="btn btn-success" id="modal-confirm">确定</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const input = overlay.querySelector('#modal-input');
        input.focus();
        input.select();
        overlay.querySelector('#modal-confirm').onclick = () => { resolve(input.value); overlay.remove(); };
        overlay.querySelector('#modal-cancel').onclick = () => { resolve(null); overlay.remove(); };
        overlay.onclick = (e) => { if (e.target === overlay) { resolve(null); overlay.remove(); } };
        input.onkeydown = (e) => {
            if (e.key === 'Enter') { resolve(input.value); overlay.remove(); }
            if (e.key === 'Escape') { resolve(null); overlay.remove(); }
        };
    });
}

/**
 * 自定义确认弹窗，替代浏览器 confirm()
 * @param {string} title 标题
 * @param {string} message 确认消息
 * @returns {Promise<boolean>} true=确定, false=取消
 */
function showConfirmModal(title, message) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);z-index:2000;display:flex;align-items:center;justify-content:center;';
        overlay.innerHTML = `
            <div class="card" style="max-width:450px;width:90%;">
                <h3 style="margin-bottom:12px;">${title}</h3>
                <p style="color:#4a5568;font-size:14px;margin-bottom:16px;line-height:1.6;white-space:pre-wrap;">${message}</p>
                <div style="display:flex;gap:8px;justify-content:flex-end;">
                    <button class="btn" id="modal-cancel" style="background:#a0aec0;">取消</button>
                    <button class="btn btn-danger" id="modal-confirm">确定</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.querySelector('#modal-confirm').onclick = () => { resolve(true); overlay.remove(); };
        overlay.querySelector('#modal-cancel').onclick = () => { resolve(false); overlay.remove(); };
        overlay.onclick = (e) => { if (e.target === overlay) { resolve(false); overlay.remove(); } };
        document.querySelector('#modal-cancel').focus();
        overlay.querySelector('#modal-cancel').addEventListener('keydown', (e) => { if (e.key === 'Escape') { resolve(false); overlay.remove(); } });
    });
}

/**
 * 格式化日期时间为可读字符串
 * @param {string|Date} dt - 日期对象或日期字符串
 * @returns {string} 格式化后的日期时间
 */
function formatDate(dt) {
    if (!dt) return '-';
    const date = new Date(dt);
    if (isNaN(date.getTime())) return dt;
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const min = String(date.getMinutes()).padStart(2, '0');
    return `${y}-${m}-${d} ${h}:${min}`;
}

/**
 * 格式化金额显示
 * @param {number} amount - 金额数值
 * @param {string} direction - 方向：'支出' 或 '收入'
 * @returns {string} 带颜色样式的 HTML 字符串
 */
function formatAmount(amount, direction) {
    const num = parseFloat(amount) || 0;
    if (direction === '收入' || direction === 'income' || direction === 'in') {
        return `<span class="amount-negative">+${num.toFixed(2)}</span>`;
    }
    if (direction === '中性' || direction === 'neutral') {
        return `<span>${num.toFixed(2)}</span>`;
    }
    return `<span class="amount-positive">-${num.toFixed(2)}</span>`;
}

/**
 * 显示浮动提示消息
 * @param {string} msg - 提示内容
 * @param {string} type - 类型：'success' 或 'error'
 */
function showAlert(msg, type) {
    // 移除已有提示
    const old = document.querySelector('.alert');
    if (old) old.remove();

    const div = document.createElement('div');
    div.className = `alert alert-${type}`;
    div.textContent = msg;
    document.body.appendChild(div);

    // 3秒后自动消失
    setTimeout(() => {
        div.style.opacity = '0';
        div.style.transition = 'opacity 0.3s';
        setTimeout(() => div.remove(), 300);
    }, 3000);
}

/**
 * 发送 JSON 请求（PUT / POST）
 * @param {string} url - 请求地址
 * @param {string} method - 请求方法（PUT / POST / DELETE）
 * @param {object|null} body - 请求体对象
 * @returns {Promise<object>} 响应 JSON
 */
async function jsonRequest(url, method, body) {
    const options = {
        method: method,
        headers: {
            'Content-Type': 'application/json'
        }
    };
    if (body && method !== 'DELETE') {
        options.body = JSON.stringify(body);
    }
    const res = await fetch(url, options);
    const data = await res.json();
    if (!res.ok) {
        throw new Error(data.detail || data.message || '请求失败');
    }
    return data;
}

/**
 * 获取当前月份字符串 (YYYY-MM)
 * @returns {string}
 */
function getCurrentMonth() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    return `${y}-${m}`;
}

/**
 * 获取当月第一天日期字符串 (YYYY-MM-DD)
 * @returns {string}
 */
function getMonthStart() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    return `${y}-${m}-01`;
}

/**
 * 更新导航栏待分类徽章数量
 */
async function updatePendingBadge() {
    try {
        const res = await fetch('/api/pending?page=1&per_page=1');
        const data = await res.json();
        const badge = document.getElementById('pending-count');
        if (badge) {
            if (data.total > 0) {
                badge.textContent = data.total;
                badge.style.display = 'inline';
            } else {
                badge.style.display = 'none';
            }
        }
    } catch (err) {
        console.error('获取待分类数量失败:', err);
    }
}

/* ===== 全局账期（日期范围）===== */

const GLOBAL_RANGE_KEY = 'global_date_range';

/**
 * 获取当前全局账期
 * @returns {{start: string, end: string}|null} 返回 {start, end}（YYYY-MM-DD），null 表示全部
 */
function getGlobalDateRange() {
    try {
        const raw = localStorage.getItem(GLOBAL_RANGE_KEY);
        if (!raw) return null;
        const range = JSON.parse(raw);
        if (range && range.start && range.end) return range;
        return null;
    } catch (err) {
        return null;
    }
}

/**
 * 设置全局账期并广播变更事件
 * @param {{start: string, end: string}|null} range - 为 null 表示全部
 */
function setGlobalDateRange(range) {
    if (range && range.start && range.end) {
        localStorage.setItem(GLOBAL_RANGE_KEY, JSON.stringify(range));
    } else {
        localStorage.removeItem(GLOBAL_RANGE_KEY);
        range = null;
    }
    window.dispatchEvent(new CustomEvent('global-range-changed', { detail: range }));
}

/**
 * 构造带账期参数的 URL
 * @param {string} url - 原始 URL
 * @returns {string} 附加 start_date/end_date 参数后的 URL
 */
function withGlobalRange(url) {
    const range = getGlobalDateRange();
    if (!range) return url;
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}start_date=${range.start}&end_date=${range.end}`;
}

/**
 * 初始化全局账期条 UI（默认当月）
 */
function initGlobalRangeBar() {
    const startInput = document.getElementById('global-range-start');
    const endInput = document.getElementById('global-range-end');
    const hint = document.getElementById('global-range-hint');
    if (!startInput || !endInput) return;

    // 首次使用默认当月
    if (!getGlobalDateRange()) {
        const now = new Date();
        const y = now.getFullYear();
        const m = String(now.getMonth() + 1).padStart(2, '0');
        const lastDay = new Date(y, now.getMonth() + 1, 0).getDate();
        setGlobalDateRange({
            start: `${y}-${m}-01`,
            end: `${y}-${m}-${String(lastDay).padStart(2, '0')}`,
        });
    }

    function syncInputs() {
        const range = getGlobalDateRange();
        if (range) {
            startInput.value = range.start;
            endInput.value = range.end;
            hint.textContent = `当前账期：${range.start} ~ ${range.end}`;
            hint.style.color = '#2b6cb0';
        } else {
            startInput.value = '';
            endInput.value = '';
            hint.textContent = '当前账期：全部数据';
            hint.style.color = '#718096';
        }
    }
    syncInputs();

    document.getElementById('btn-range-apply').addEventListener('click', () => {
        if (!startInput.value || !endInput.value) {
            showAlert('请选择完整的开始和结束日期', 'error');
            return;
        }
        if (startInput.value > endInput.value) {
            showAlert('开始日期不能晚于结束日期', 'error');
            return;
        }
        setGlobalDateRange({ start: startInput.value, end: endInput.value });
    });

    document.getElementById('btn-range-this-month').addEventListener('click', () => {
        const now = new Date();
        const y = now.getFullYear();
        const m = String(now.getMonth() + 1).padStart(2, '0');
        const lastDay = new Date(y, now.getMonth() + 1, 0).getDate();
        setGlobalDateRange({
            start: `${y}-${m}-01`,
            end: `${y}-${m}-${String(lastDay).padStart(2, '0')}`,
        });
        syncInputs();
    });

    document.getElementById('btn-range-prev-month').addEventListener('click', () => {
        const now = new Date();
        const y = now.getFullYear();
        const m = now.getMonth(); // 0-based
        const prevFirst = new Date(y, m - 1, 1);
        const py = prevFirst.getFullYear();
        const pm = String(prevFirst.getMonth() + 1).padStart(2, '0');
        const lastDay = new Date(py, prevFirst.getMonth() + 1, 0).getDate();
        setGlobalDateRange({
            start: `${py}-${pm}-01`,
            end: `${py}-${pm}-${String(lastDay).padStart(2, '0')}`,
        });
        syncInputs();
    });

    document.getElementById('btn-range-clear').addEventListener('click', () => {
        setGlobalDateRange(null);
        syncInputs();
    });
}

// 页面加载时自动更新待分类徽章
document.addEventListener('DOMContentLoaded', function () {
    updatePendingBadge();
    initGlobalRangeBar();
});
