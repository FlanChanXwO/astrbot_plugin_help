/**
 * 应用入口 - 直接创建响应式 store 并作为根作用域
 */
import {createCustomGroup, deleteCustomGroup, getCustomGroups, ready, updateCustomGroup,} from './js/api.js';
import {initTheme} from './js/theme.js';

let toastTimer = null;
let cmdKey = 0;

function newCommand(type = 'command') {
    return {
        _key: cmdKey++,
        type,
        command: '',
        pattern: '',
        aliases: [],
        examples: [],
        is_admin: false,
        hidden: false,
    };
}

// Create reactive store
const store = PetiteVue.reactive({
    // State
    groups: [],
    panelVisible: false,
    panelExpanded: false,
    editingIndex: -1,
    toast: {show: false, message: '', type: 'success'},
    dialog: {show: false, title: '', message: '', okText: '确定', okClass: 'btn-danger', resolve: null},
    form: {
        groupName: '',
        groupDesc: '',
        priority: 0,
        hidden: false,
        commands: [newCommand()],
    },

    // Draft data for create mode (to prevent accidental loss)
    draft: null,

    // Actions
    async loadGroups() {
        try {
            const result = await getCustomGroups();
            const data = Array.isArray(result) ? result : [];
            this.groups = data;
        } catch (err) {
            this.showToast('加载分组失败: ' + err.message, 'error');
        }
    },

    openPanel(index = -1) {
        this.editingIndex = index;
        if (index >= 0) {
            const g = this.groups[index];
            this.form.groupName = g.group_name || '';
            this.form.groupDesc = g.description || '';
            this.form.priority = g.priority || 0;
            this.form.hidden = g.hidden || false;
            this.form.commands = (g.commands || []).map((c) => ({
                ...c,
                _key: cmdKey++,
                aliases: [...(c.aliases || [])],
                examples: [...(c.examples || [])],
            }));
        } else {
            this._restoreDraft();
        }
        this.panelVisible = true;
    },

    _saveDraft() {
        this.draft = {
            groupName: this.form.groupName,
            groupDesc: this.form.groupDesc,
            priority: this.form.priority,
            hidden: this.form.hidden,
            commands: this.form.commands.map(c => ({
                ...c,
                aliases: [...c.aliases],
                examples: [...c.examples],
            })),
        };
    },

    _restoreDraft() {
        if (this.draft) {
            const d = this.draft;
            this.form.groupName = d.groupName;
            this.form.groupDesc = d.groupDesc;
            this.form.priority = d.priority;
            this.form.hidden = d.hidden;
            this.form.commands = d.commands.map(c => ({
                ...c,
                _key: cmdKey++,
                aliases: [...c.aliases],
                examples: [...c.examples],
            }));
            this.showToast('已恢复上次未保存的表单', 'success', 2000);
        } else {
            this.resetForm();
        }
    },

    _clearDraft() {
        this.draft = null;
    },

    closePanel() {
        if (this.editingIndex < 0) {
            this._saveDraft();
        }
        this.panelVisible = false;
        this.editingIndex = -1;
    },

    toggleExpand() {
        this.panelExpanded = !this.panelExpanded;
    },

    resetForm() {
        // 重置表单数据（不显示确认对话框）
        this.form.groupName = '';
        this.form.groupDesc = '';
        this.form.priority = 0;
        this.form.hidden = false;
        this.form.commands = []; // 默认不给空命令
    },

    async confirmAndReset() {
        // 重置按钮点击时显示确认对话框
        const ok = await this.showConfirm('确定要重置表单吗？这将清除所有输入的内容。', '重置表单', '重置', 'btn-danger');
        if (!ok) return;
        this._clearDraft();
        this.resetForm();
        this.showToast('表单已重置', 'success', 2000);
    },

    addCommand() {
        this.form.commands.push(newCommand());
    },

    removeCommand(i) {
        this.form.commands.splice(i, 1);
    },

    showToast(msg, type = 'success', duration = 3000) {
        if (toastTimer) clearTimeout(toastTimer);
        this.toast.show = true;
        this.toast.message = msg;
        this.toast.type = type;
        toastTimer = setTimeout(() => {
            this.toast.show = false;
        }, duration);
    },

    async showConfirm(message, title = '确认', okText = '确定', okClass = 'btn-danger') {
        this.dialog.title = title;
        this.dialog.message = message;
        this.dialog.okText = okText;
        this.dialog.okClass = okClass;
        this.dialog.show = true;

        return new Promise((resolve) => {
            this.dialog.resolve = (result) => {
                this.dialog.show = false;
                resolve(result);
            };
        });
    },

    async save() {
        if (!this.form.groupName.trim()) {
            this.showToast('请输入分组名称', 'error');
            return;
        }
        for (const cmd of this.form.commands) {
            if (cmd.type === 'command' && !cmd.command.trim()) {
                this.showToast('请填写命令名称', 'error');
                return;
            }
            if (cmd.type === 'regex' && !cmd.pattern.trim()) {
                this.showToast('请填写正则匹配模式', 'error');
                return;
            }
        }
        const cmdList = this.form.commands.map((cmd) => {
            const base = {is_admin: cmd.is_admin, hidden: cmd.hidden};
            if (cmd.type === 'command') {
                return {...base, type: 'command', command: cmd.command.trim(), aliases: [...cmd.aliases]};
            }
            return {...base, type: 'regex', pattern: cmd.pattern.trim(), examples: [...cmd.examples]};
        });
        const data = {
            group_name: this.form.groupName.trim(),
            description: this.form.groupDesc.trim(),
            priority: this.form.priority || 0,
            hidden: this.form.hidden || false,
            commands: cmdList,
        };
        try {
            if (this.editingIndex >= 0) {
                await updateCustomGroup(this.editingIndex, data);
            } else {
                await createCustomGroup(data);
            }
            this.showToast(this.editingIndex >= 0 ? '分组已更新' : '分组已创建');
            this._clearDraft();
            this.resetForm();
            this.closePanel();
            await this.loadGroups();
        } catch (err) {
            this.showToast('保存失败: ' + err.message, 'error');
        }
    },

    async handleDelete() {
        if (this.editingIndex < 0) return;
        const ok = await this.showConfirm('确定要删除这个分组吗？此操作不可恢复。', '删除分组', '删除', 'btn-danger');
        if (!ok) return;
        try {
            await deleteCustomGroup(this.editingIndex);
            this.showToast('分组已删除');
            this._clearDraft();
            this.resetForm();
            this.closePanel();
            await this.loadGroups();
        } catch (err) {
            this.showToast('删除失败: ' + err.message, 'error');
        }
    },
});

// Expose to window for debugging
window.store = store;

// Init
initTheme();
ready()
    .then(() => store.loadGroups())
    .catch((err) => store.showToast('初始化失败: ' + err.message, 'error'));

// Mount petite-vue - use store as the root scope data
PetiteVue.createApp(store).mount('.container');
