# Selected nebula identity in the existing portal client

User selected C (blue nebula core with one diagonal orbit and upper-right satellite), and clarified that the deliverable is the existing web client with an additional documentation section.

- [x] Preserve the selected graphic in a transparent main mark and wordmark; make a faithful small-size favicon adaptation using built-in GPT Image. Keep sources and exact prompts.
- [x] Wire the selected identity into the shared navigation, browser icons and actual login page. Preserve dynamic portal naming, form fields/actions, authentication, link destinations and role conditions.
- [x] Refine existing resource/status/profile presentation: clear heading groups and a resource-list section; compact quota spacing without losing fields. Keep all business selectors and operations.
- [x] Name the added navigation section “使用文档”; retain `/guide` inside the same Flask/Jinja site. Do not make it the homepage or alter home/login redirects.
- [x] Verify existing pages, login, resource actions and responsive layout; run full pytest and existing web smoke. Open the authenticated “我的资源” preview for the user, then commit/push the feature branch. Do not deploy production.
