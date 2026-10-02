export const trackFavoritesEn = {
  only: 'Favorites only', add: 'Add {name} to favorites', remove: 'Remove {name} from favorites',
  saving: 'Saving favorite…', loading: 'Loading favorites…', saveFailed: 'This favorite could not be saved. Try again.',
  loadFailed: 'Favorites could not be loaded.', retry: 'Retry', noneMatching: 'No favorite tracks match these filters.',
}
export const trackFavoritesRu: { [K in keyof typeof trackFavoritesEn]: string } = {
  only: 'Только избранное', add: 'Добавить «{name}» в избранное', remove: 'Убрать «{name}» из избранного',
  saving: 'Сохраняем избранное…', loading: 'Загружаем избранное…', saveFailed: 'Не удалось сохранить избранное. Попробуйте снова.',
  loadFailed: 'Не удалось загрузить избранное.', retry: 'Повторить', noneMatching: 'Нет избранных треков, соответствующих фильтрам.',
}
