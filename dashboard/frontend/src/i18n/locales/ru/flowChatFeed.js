export default {
  added: 'добавлено',
  // Подпись свёрнутой мысли в ленте чата.
  thought: 'Мысль',
  // «и ещё изменений: 4» — хвост списка правок, который не поместился целиком.
  moreChanges: 'и ещё изменений: {{count}}',
  // Что агент сделал с редактируемой сущностью — так это выглядит в чате.
  change: {
    added: 'добавлено',
    removed: 'удалено',
    updated: 'изменено',
    set: 'задано',
  },
  // К чему это относится. В единственном числе: строка описывает одну сущность.
  kinds: {
    locations: 'локация',
    items: 'предмет',
    entities: 'объект',
    globals: 'общий параметр',
    stats: 'характеристика',
    roles: 'роль',
    actions: 'действие',
    objectives: 'цель',
    rules: 'правила',
    base_actions: 'встроенные действия',
    end_when: 'условия завершения',
    name: 'название',
    description: 'описание',
    starting_location: 'стартовая локация',
    time_of_day: 'время суток',
    hours_per_tick: 'часов за такт',
    environment: 'среда',
    activation: 'активация',
    env_params: 'параметр',
    limits: 'ограничение',
  },
  // Куда записал шаг remember или forget: «Сохранено в личную память · dev».
  memory: {
    remember: { personal: 'Сохранено в личную память · {{workspace}}', pool: 'Сохранено в память «{{pool}}» · {{workspace}}' },
    forget: { personal: 'Удалено из личной памяти · {{workspace}}', pool: 'Удалено из памяти «{{pool}}» · {{workspace}}' },
  },
};
