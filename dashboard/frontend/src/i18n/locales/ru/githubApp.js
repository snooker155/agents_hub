// Карточка GitHub App на странице Git-коннектора (routes/github_app.py,
// docs/github-app.md).
export default {
  title: 'GitHub App',
  description: 'С GitHub App хаб сам выпускает короткоживущие токены. Привяжите установку к рабочему пространству, и каждый запуск в нём, чей агент объявляет GITHUB_TOKEN, будет пушить и открывать pull request от имени приложения.',
  notConfigured: 'На этом хабе не настроено.',
  envHint: 'Создайте приложение на GitHub, затем задайте эти переменные в окружении бэкенда и перезапустите его:',
  install: 'Установить',
  sync: 'Синхронизировать',
  noInstallations: 'Установок пока нет. Установите приложение в аккаунт или организацию, затем синхронизируйте.',
  account: 'Аккаунт',
  type: 'Тип',
  repositories: 'Репозитории',
  workspace: 'Рабочее пространство',
  unbound: 'Не привязано',
  unbind: 'Отвязать',
  bindTo: 'Рабочее пространство для {{account}}',
  types: {
    Organization: 'Организация',
    User: 'Пользователь',
  },
  targets: {
    all: 'Все',
    selected: 'Выбранные',
  },
  loadFailed: 'Не удалось загрузить GitHub App.',
  syncFailed: 'Не удалось синхронизировать установки.',
  bindFailed: 'Не удалось изменить привязку.',
};
