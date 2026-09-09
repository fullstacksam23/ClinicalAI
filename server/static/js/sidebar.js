import {
  api,
  esc
} from './utils.js';

export async function renderSidebarChats(
  activeId = null
) {
  const list =
    document.querySelector(
      '#chat-list'
    );

  if (!list) return;

  try {
    const conversations =
      await api(
        '/conversations'
      );

    list.innerHTML =
      conversations.length
        ? conversations
            .map(
              c => `
                <div
                  class="chat-item ${
                    c.id === activeId
                      ? 'active'
                      : ''
                  }"
                  data-chat-id="${esc(
                    c.id
                  )}">

                  <span
                    class="chat-item-title">

                    ${esc(c.title)}

                  </span>

                  <button
                    class="chat-item-delete"
                    type="button"
                    data-delete-chat="${esc(
                      c.id
                    )}"
                    title="Delete chat"
                    aria-label="Delete chat">

                    ×

                  </button>

                </div>
              `
            )
            .join('')
        : `
          <div class="chat-list-empty">
            No chats yet
          </div>
        `;

  } catch {
    list.innerHTML = `
      <div class="chat-list-empty">
        Couldn’t load chats
      </div>
    `;
  }
}