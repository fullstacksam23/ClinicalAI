import {
  api,
  esc,
  toast
} from './utils.js';

import {
  view,
  title,
  state
} from './state.js';

import {
  cleanupPdfBlob
} from './pdf.js';

import {
  messageHtml
} from './citations.js';

import {
  renderSidebarChats
} from './sidebar.js';

export async function chat(
  id = null
) {
  cleanupPdfBlob();

  title.textContent =
    'Ask ClinicalAI';

  state.currentConversationId =
    id || null;

  view.innerHTML = `
    <section class="chat">

      <div class="chat-head">

        <h2>
          Ask about your health records
        </h2>

        <p class="muted">
          ClinicalAI searches your uploaded
          documents. It is not a substitute
          for professional medical advice.
        </p>

      </div>

      <div
        class="messages"
        id="messages">

        <div
          class="${
            id
              ? 'loading'
              : 'empty'
          }">

          ${
            id
              ? 'Loading chat…'
              : `
                <strong>
                  How can I help?
                </strong>

                Ask a question about a report,
                a result, or your record history.
              `
          }

        </div>

      </div>

      <form
        class="composer"
        id="chat-form">

        <textarea
          id="question"
          placeholder="Ask about your health records…"
          aria-label="Question"></textarea>

        <button class="button">
          Send
        </button>

      </form>

    </section>
  `;

  const form =
    document.querySelector(
      '#chat-form'
    );

  const input =
    document.querySelector(
      '#question'
    );

  const messages =
    document.querySelector(
      '#messages'
    );

  if (id) {
    try {
      const conversation =
        await api(
          '/conversations/' +
            encodeURIComponent(id)
        );

      const msgs =
        conversation.messages ||
        [];

      messages.innerHTML =
        msgs.length
          ? msgs
              .map(m =>
                messageHtml(
                  m.role,
                  m.content,
                  m.sources
                )
              )
              .join('')
          : `
            <div class="empty">

              <strong>
                How can I help?
              </strong>

              Ask a question about a report,
              a result, or your record history.

            </div>
          `;

    } catch (e) {
      messages.innerHTML = `
        <div class="empty">

          <strong>
            Couldn’t load this chat
          </strong>

          ${esc(e.message)}

        </div>
      `;
    }

    messages.scrollTop =
      messages.scrollHeight;
  }

  input.value = '';

  input.onkeydown = e => {
    if (
      e.key === 'Enter' &&
      !e.shiftKey
    ) {
      e.preventDefault();
      form.requestSubmit();
    }
  };

  form.onsubmit = async e => {
    e.preventDefault();

    const q =
      input.value.trim();

    if (!q) return;

    messages
      .querySelector(
        '.empty'
      )
      ?.remove();

    messages.insertAdjacentHTML(
      'beforeend',
      `
        <div
          class="message-row user-row">

          <div class="message user">
            ${esc(q)}
          </div>

        </div>

        <div
          class="message-row assistant-row"
          id="pending-row">

          <div class="message assistant">
            Searching your record…
          </div>

        </div>
      `
    );

    input.value = '';

    messages.scrollTop =
      messages.scrollHeight;

    try {
      const r =
        await api(
          '/chat',
          {
            method: 'POST',

            headers: {
              'Content-Type':
                'application/json'
            },

            body:
              JSON.stringify({
                question: q,
                conversation_id:
                  state.currentConversationId ||
                  null
              })
          }
        );

      const pending =
        document.querySelector(
          '#pending-row'
        );

      if (pending) {
        pending.outerHTML =
          messageHtml(
            'assistant',
            r.answer,
            r.sources
          );
      }

      if (
        r.conversation_id &&
        r.conversation_id !==
          state.currentConversationId
      ) {
        state.currentConversationId =
          r.conversation_id;

        history.replaceState(
          null,
          '',
          '#chat/' +
            encodeURIComponent(
              state.currentConversationId
            )
        );
      }

      await renderSidebarChats(
        state.currentConversationId
      );

    } catch (err) {

      const pending =
        document.querySelector(
          '#pending-row'
        );

      if (pending) {
        pending.outerHTML = `
          <div
            class="message-row assistant-row">

            <div class="message assistant">

              I couldn’t complete
              that request.

              <br>

              ${esc(err.message)}

              <br>

              <button
                class="link-button"
                data-retry="${esc(q)}">
                Retry
              </button>

            </div>

          </div>
        `;
      }
    }

    messages.scrollTop =
      messages.scrollHeight;
  };
}

export function newChat() {
  state.currentConversationId =
    null;

  location.hash =
    '#chat';
}

export async function deleteChat(
  chatId
) {
  try {
    await api(
      '/conversations/' +
        encodeURIComponent(chatId),
      {
        method: 'DELETE'
      }
    );

    if (
      chatId ===
      state.currentConversationId
    ) {
      state.currentConversationId =
        null;

      location.hash =
        '#chat';

    } else {
      await renderSidebarChats(
        state.currentConversationId
      );
    }

  } catch (err) {
    toast(err.message);
  }
}