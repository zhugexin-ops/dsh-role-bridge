/* dsh-role-bridge —— 浏览器半边：侧栏按钮 + 「叫她」浮层
 *
 * 只有界面，不碰引擎。所有网络请求都打到宿主插件的 /role-bridge/api（同源）。
 * 加载期任何异常都必须被吞掉：这个文件挂了会把浏览器整边拖下去。
 */
window.__ModuleLoader__.load({
  id: 'dsh-role-bridge',
  factory: function (require) {
    var module = { exports: {} };
    var exports = module.exports;

    var inject = ['slots'];
    var apply = function () {};

    try {
      // ---------- React（拿不到就退化，绝不抛） ----------
      function shellRequire(id) {
        try {
          if (typeof __require === 'function') {
            var mod = __require(id);
            if (mod != null) return mod;
          }
        } catch (e) {}
        try {
          if (typeof require === 'function') {
            var mod2 = require(id);
            if (mod2 != null) return mod2;
          }
        } catch (e) {}
        return null;
      }

      var React = shellRequire('react');
      if (!React && globalThis && globalThis.React) React = globalThis.React;

      var createElement = React && React.createElement ? React.createElement : function () { return null; };
      var useState = React && React.useState ? React.useState : function (v) { return [v, function () {}]; };
      var useEffect = React && React.useEffect ? React.useEffect : function () {};
      var useRef = React && React.useRef ? React.useRef : function (v) { return { current: v }; };

      function h() {
        try {
          return createElement.apply(null, arguments);
        } catch (e) {
          return null;
        }
      }

      // ---------- 本地路由（必须同源，走页面基址） ----------
      var PLACEHOLDER_ORIGIN = 'http://dsh.internal/';

      function directoryBase(baseUrl) {
        try {
          var url = new URL(baseUrl, PLACEHOLDER_ORIGIN);
          url.search = '';
          url.hash = '';
          if (!url.pathname.endsWith('/')) url.pathname += '/';
          return url;
        } catch (e) {
          return new URL(PLACEHOLDER_ORIGIN);
        }
      }

      function hostHttpBase() {
        try {
          if (typeof document !== 'undefined' && document && document.baseURI) return document.baseURI;
        } catch (e) {}
        return PLACEHOLDER_ORIGIN;
      }

      function hostRouteUrl(path) {
        return new URL(String(path).replace(/^\/+/, ''), directoryBase(hostHttpBase()));
      }

      function api(action, payload) {
        var url = hostRouteUrl('role-bridge/api/' + action).href;
        var controller = null;
        var timer = null;
        try {
          if (typeof AbortController === 'function') controller = new AbortController();
        } catch (e) {}
        var options = {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-role-bridge': '1' },
          body: JSON.stringify(payload || {}),
        };
        if (controller) {
          options.signal = controller.signal;
          timer = setTimeout(function () {
            try {
              controller.abort();
            } catch (e) {}
          }, 180000);
        }
        return fetch(url, options)
          .then(function (res) {
            return res.json().then(
              function (data) {
                return data;
              },
              function () {
                return { ok: false, error: '回包不是 JSON（' + res.status + '）' };
              },
            );
          })
          .then(function (data) {
            if (timer) clearTimeout(timer);
            return data;
          })
          .catch(function (err) {
            if (timer) clearTimeout(timer);
            throw err;
          });
      }

      // ---------- 面板开关（模块级小 store） ----------
      var visible = false;
      var listeners = [];
      var ACTIVE_EVENT = 'dsh-panel-activate';

      function notify() {
        var snapshot = listeners.slice();
        for (var i = 0; i < snapshot.length; i += 1) {
          try {
            snapshot[i]();
          } catch (e) {}
        }
      }

      function setVisible(next) {
        visible = !!next;
        notify();
        if (visible) {
          try {
            document.dispatchEvent(new CustomEvent(ACTIVE_EVENT, { detail: 'role-bridge' }));
          } catch (e) {}
        }
      }

      function toggleVisible() {
        setVisible(!visible);
      }

      function subscribe(fn) {
        listeners.push(fn);
        return function () {
          listeners = listeners.filter(function (item) {
            return item !== fn;
          });
        };
      }

      function useVisible() {
        var pair = useState(visible);
        var value = pair[0];
        var setValue = pair[1];
        useEffect(function () {
          var off = subscribe(function () {
            setValue(visible);
          });
          setValue(visible);
          return function () {
            off();
          };
        }, []);
        return value;
      }

      // 别的面板打开时关掉自己
      try {
        document.addEventListener(ACTIVE_EVENT, function (event) {
          try {
            var detail = event && event.detail;
            if (detail && detail !== 'role-bridge' && visible) setVisible(false);
          } catch (e) {}
        });
      } catch (e) {}

      // ---------- 样式 ----------
      var COLOR = {
        text: 'var(--dsw-alias-text-primary, #e8e9ec)',
        dim: 'var(--dsw-alias-text-secondary, #9aa0a8)',
        line: 'var(--dsw-alias-border-weak, rgba(255,255,255,0.12))',
        card: 'var(--dsw-alias-bg-elevated, #1c1e23)',
        sub: 'var(--dsw-alias-bg-subtle, rgba(255,255,255,0.05))',
        accent: 'var(--dsw-alias-accent, #6b8afd)',
      };

      var S = {
        card: {
          position: 'fixed',
          right: '20px',
          bottom: '20px',
          width: '420px',
          maxWidth: 'calc(100vw - 40px)',
          height: 'min(620px, calc(100vh - 40px))',
          display: 'flex',
          flexDirection: 'column',
          background: COLOR.card,
          color: COLOR.text,
          border: '1px solid ' + COLOR.line,
          borderRadius: '14px',
          boxShadow: '0 24px 60px rgba(0,0,0,0.45)',
          pointerEvents: 'auto',
          overflow: 'hidden',
          font: '13px/1.6 var(--dsw-alias-font-sans, system-ui, "Microsoft YaHei", sans-serif)',
        },
        header: {
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 12px',
          borderBottom: '1px solid ' + COLOR.line,
        },
        title: { fontWeight: 600, fontSize: '13px', whiteSpace: 'nowrap' },
        select: {
          flex: 1,
          minWidth: 0,
          background: COLOR.sub,
          color: COLOR.text,
          border: '1px solid ' + COLOR.line,
          borderRadius: '8px',
          padding: '4px 6px',
          fontSize: '12px',
        },
        iconButton: {
          background: 'transparent',
          color: COLOR.dim,
          border: 'none',
          cursor: 'pointer',
          fontSize: '15px',
          lineHeight: 1,
          padding: '2px 6px',
          borderRadius: '6px',
        },
        body: { flex: 1, overflowY: 'auto', padding: '12px', display: 'flex', flexDirection: 'column', gap: '8px' },
        bubbleMine: {
          alignSelf: 'flex-end',
          maxWidth: '85%',
          background: COLOR.accent,
          color: '#fff',
          padding: '7px 10px',
          borderRadius: '12px 12px 2px 12px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        },
        bubbleHers: {
          alignSelf: 'flex-start',
          maxWidth: '85%',
          background: COLOR.sub,
          color: COLOR.text,
          padding: '7px 10px',
          borderRadius: '12px 12px 12px 2px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        },
        bubbleSys: { alignSelf: 'center', color: COLOR.dim, fontSize: '11px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
        composer: { borderTop: '1px solid ' + COLOR.line, padding: '8px 10px', display: 'flex', gap: '8px' },
        textarea: {
          flex: 1,
          resize: 'none',
          minHeight: '40px',
          maxHeight: '120px',
          background: COLOR.sub,
          color: COLOR.text,
          border: '1px solid ' + COLOR.line,
          borderRadius: '10px',
          padding: '8px 10px',
          fontSize: '13px',
          fontFamily: 'inherit',
          outline: 'none',
        },
        send: {
          alignSelf: 'flex-end',
          background: COLOR.accent,
          color: '#fff',
          border: 'none',
          borderRadius: '10px',
          padding: '9px 14px',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: 600,
        },
        toolbar: {
          display: 'flex',
          gap: '6px',
          padding: '6px 10px',
          borderTop: '1px solid ' + COLOR.line,
          alignItems: 'center',
          flexWrap: 'wrap',
        },
        toolButton: {
          background: COLOR.sub,
          color: COLOR.dim,
          border: '1px solid ' + COLOR.line,
          borderRadius: '8px',
          padding: '3px 8px',
          cursor: 'pointer',
          fontSize: '11px',
        },
        pre: {
          margin: 0,
          padding: '10px',
          background: 'rgba(0,0,0,0.35)',
          color: '#cfd3da',
          fontSize: '11px',
          lineHeight: 1.5,
          overflow: 'auto',
          maxHeight: '240px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          borderTop: '1px solid ' + COLOR.line,
        },
        entry: {
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '6px',
          width: '100%',
          background: 'transparent',
          color: 'var(--dsw-alias-text-secondary, #9aa0a8)',
          border: '1px solid var(--dsw-alias-border-weak, rgba(255,255,255,0.12))',
          borderRadius: '8px',
          padding: '6px 8px',
          cursor: 'pointer',
          fontSize: '12px',
          fontFamily: 'inherit',
        },
      };

      // ---------- 侧栏入口按钮 ----------
      function EntryButton(props) {
        try {
          var wide = !props || props.wide !== false;
          return h(
            'button',
            {
              type: 'button',
              style: S.entry,
              title: '叫她（本地引擎，不进对话）',
              onClick: function () {
                toggleVisible();
              },
            },
            wide ? '叫她' : '她',
          );
        } catch (e) {
          return null;
        }
      }

      // ---------- 面板 ----------
      function Panel() {
        var isOpen = useVisible();
        var rolesPair = useState([]);
        var roles = rolesPair[0];
        var setRoles = rolesPair[1];
        var rolePair = useState('');
        var role = rolePair[0];
        var setRole = rolePair[1];
        var msgPair = useState([]);
        var messages = msgPair[0];
        var setMessages = msgPair[1];
        var draftPair = useState('');
        var draft = draftPair[0];
        var setDraft = draftPair[1];
        var busyPair = useState(false);
        var busy = busyPair[0];
        var setBusy = busyPair[1];
        var detailPair = useState(null);
        var detail = detailPair[0];
        var setDetail = detailPair[1];
        var problemPair = useState('');
        var problem = problemPair[0];
        var setProblem = problemPair[1];
        var exoPair = useState(null);
        var exo = exoPair[0];
        var setExo = exoPair[1];

        function toggleExo() {
          var next = !(exo && exo.enabled);
          api('exo-toggle', { enabled: next })
            .then(function (data) {
              if (data && data.ok) setExo(data);
            })
            .catch(function (err) {
              setProblem('外骨骼开关没成功：' + ((err && err.message) || err));
            });
        }

        function appendMessage(kind, text) {
          setMessages(function (prev) {
            return prev.concat([{ who: kind, text: String(text == null ? '' : text) }]);
          });
        }

        function loadStatus() {
          api('status', {})
            .then(function (data) {
              if (!data || !data.roles) {
                setProblem('本地服务没有回话。');
                return;
              }
              setRoles(data.roles || []);
              setProblem('');
              if (!role && data.roles.length) {
                setRole(data.roles.indexOf('大肥鱼') >= 0 ? '大肥鱼' : data.roles[0]);
              }
              api('exo', {})
                .then(function (info) {
                  if (info && info.ok) setExo(info);
                })
                .catch(function () {});
            })
            .catch(function (err) {
              setProblem('连不上本地服务：' + ((err && err.message) || err));
            });
        }

        useEffect(
          function () {
            if (isOpen) loadStatus();
          },
          [isOpen],
        );

        function send() {
          var text = String(draft || '').trim();
          if (!text || busy) return;
          if (!role) {
            appendMessage('sys', '先在上面选一个角色。');
            return;
          }
          appendMessage('me', text);
          setDraft('');
          setBusy(true);
          api('ask', { role: role, text: text, speaker: '我' })
            .then(function (data) {
              var reply = data && typeof data.reply === 'string' ? data.reply : '';
              if (!reply) {
                appendMessage('sys', '没有拿到回话：' + ((data && (data.error || data.diag)) || '未知原因'));
                return;
              }
              appendMessage(data.ok ? 'her' : 'sys', reply);
              if (data.diag) appendMessage('sys', '诊断：' + data.diag);
            })
            .catch(function (err) {
              appendMessage('sys', '连不上本地服务：' + ((err && err.message) || err));
            })
            .then(function () {
              setBusy(false);
            });
        }

        function showDoctor() {
          if (!role) return;
          setBusy(true);
          api('doctor', { role: role })
            .then(function (data) {
              setDetail({ title: '体检｜' + role, text: (data && data.text) || '没有输出' });
            })
            .catch(function (err) {
              setDetail({ title: '体检失败', text: String((err && err.message) || err) });
            })
            .then(function () {
              setBusy(false);
            });
        }

        function showLog() {
          if (!role) return;
          api('log', { role: role, lines: 80 })
            .then(function (data) {
              setDetail({ title: '轮次日志｜' + role, text: (data && data.text) || '还没有日志（这是第一次说话吗？）' });
            })
            .catch(function (err) {
              setDetail({ title: '读日志失败', text: String((err && err.message) || err) });
            });
        }

        function showShadow() {
          api('shadow', {})
            .then(function (data) {
              var entries = (data && data.entries) || [];
              if (!entries.length) {
                setDetail({ title: '影子对账', text: '还没有记录。群里有人说话后，这里会出现「收」和「发」两行，用来核对现链路和引擎。' });
                return;
              }
              var lines = entries.map(function (item) {
                return item.t + '  [' + item.kind + '] ' + (item.speaker ? item.speaker + '：' : '') + item.text;
              });
              setDetail({ title: '影子对账（只读）', text: lines.join('\n') });
            })
            .catch(function (err) {
              setDetail({ title: '读对账失败', text: String((err && err.message) || err) });
            });
        }

        if (!isOpen) return null;

        var children = [];

        children.push(
          h(
            'div',
            { key: 'header', style: S.header },
            h('span', { key: 't', style: S.title }, '叫她'),
            h(
              'select',
              {
                key: 'r',
                style: S.select,
                value: role,
                onChange: function (event) {
                  setRole(event.target.value);
                },
              },
              roles.length
                ? roles.map(function (name) {
                    return h('option', { key: name, value: name }, name);
                  })
                : h('option', { value: '' }, '（没找到角色）'),
            ),
            h(
              'button',
              {
                key: 'x',
                type: 'button',
                style: {
                  ...S.iconButton,
                  width: 'auto',
                  padding: '0 8px',
                  fontSize: '11px',
                  color: exo && exo.enabled ? '#fff' : COLOR.dim,
                  background: exo && exo.enabled ? '#2f6f4f' : 'transparent',
                  border: '1px solid ' + COLOR.line,
                },
                title: exo && exo.enabled
                  ? '外骨骼开着：在任意对话里以「@角色名」开头那句话，由她来答'
                  : '外骨骼关着：所有对话都还是大模型在答',
                onClick: toggleExo,
              },
              exo && exo.enabled ? '外骨骼·开' : '外骨骼·关',
            ),
            h(
              'button',
              {
                key: 'c',
                type: 'button',
                style: S.iconButton,
                title: '关掉',
                onClick: function () {
                  setVisible(false);
                },
              },
              '×',
            ),
          ),
        );

        var emptyHint = exo && exo.enabled
          ? '外骨骼开着。去任意对话里，用「' + (exo.trigger || '@') + '角色名 说的话」开头，那一句就由她答（大概 8–20 秒）。\n也可以就在这里直接跟她说话。'
          : '点下面的框，直接跟她说话。她大概要 8–20 秒。\n想让整个对话都由她答？点上面「外骨骼·关」打开它，然后在对话里以「' + ((exo && exo.trigger) || '@') + '角色名」开头。';
        var bodyChildren = messages.length
          ? messages.map(function (item, index) {
              var style = item.who === 'me' ? S.bubbleMine : item.who === 'her' ? S.bubbleHers : S.bubbleSys;
              return h('div', { key: 'm' + index, style: style }, item.text);
            })
          : [
              h('div', { key: 'empty', style: { ...S.bubbleSys, whiteSpace: 'pre-wrap' } }, problem || emptyHint),
            ];

        children.push(h('div', { key: 'body', style: S.body }, bodyChildren));

        children.push(
          h(
            'div',
            { key: 'composer', style: S.composer },
            h('textarea', {
              style: S.textarea,
              value: draft,
              placeholder: busy ? '她正在想…' : '说点什么（Enter 发送，Shift+Enter 换行）',
              onChange: function (event) {
                setDraft(event.target.value);
              },
              onKeyDown: function (event) {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent?.isComposing) {
                  event.preventDefault();
                  send();
                }
              },
            }),
            h(
              'button',
              {
                type: 'button',
                style: S.send,
                disabled: busy,
                onClick: function () {
                  send();
                },
              },
              busy ? '…' : '发送',
            ),
          ),
        );

        children.push(
          h(
            'div',
            { key: 'toolbar', style: S.toolbar },
            h(
              'button',
              {
                type: 'button',
                style: S.toolButton,
                onClick: showDoctor,
              },
              '体检',
            ),
            h(
              'button',
              {
                type: 'button',
                style: S.toolButton,
                onClick: showLog,
              },
              '轮次日志',
            ),
            h(
              'button',
              {
                type: 'button',
                style: S.toolButton,
                onClick: showShadow,
              },
              '影子对账',
            ),
            h(
              'button',
              {
                type: 'button',
                style: S.toolButton,
                onClick: function () {
                  setMessages([]);
                  setDetail(null);
                },
              },
              '清屏',
            ),
            h(
              'span',
              { style: { marginLeft: 'auto', color: COLOR.dim, fontSize: '11px' } },
              busy ? '在想…' : '',
            ),
          ),
        );

        if (detail) {
          children.push(
            h(
              'div',
              { key: 'detail', style: { display: 'flex', flexDirection: 'column', borderTop: '1px solid ' + COLOR.line } },
              h(
                'div',
                { style: { display: 'flex', alignItems: 'center', padding: '4px 10px', gap: '8px' } },
                h('span', { style: { fontSize: '11px', color: COLOR.dim } }, detail.title),
                h(
                  'button',
                  {
                    type: 'button',
                    style: { ...S.iconButton, marginLeft: 'auto' },
                    onClick: function () {
                      setDetail(null);
                    },
                  },
                  '×',
                ),
              ),
              h('pre', { style: S.pre }, detail.text),
            ),
          );
        }

        return h('div', { style: S.card }, children);
      }

      apply = function (ctx) {
        try {
          var slots = ctx && ctx.slots;
          if (!slots) return;
          ctx.effect(function () {
            return slots.inject('sidebar.footer.action', function () {
              return slots.register(
                { name: 'sidebar.footer.action', id: 'role-bridge-entry', order: 60, label: '叫她' },
                EntryButton,
              );
            });
          }, 'dsh-role-bridge: sidebar entry');
          ctx.effect(function () {
            return slots.inject('shell.overlay', function () {
              return slots.register(
                { name: 'shell.overlay', id: 'role-bridge-panel', order: 120, label: '叫她' },
                Panel,
              );
            });
          }, 'dsh-role-bridge: panel');
        } catch (e) {
          try {
            console.error('[role-bridge] apply failed', e);
          } catch (e2) {}
        }
      };
    } catch (error) {
      try {
        console.error('[role-bridge] client failed to load', error);
      } catch (e) {}
    }

    module.exports = { inject: inject, apply: apply };
    return module.exports;
  },
});
