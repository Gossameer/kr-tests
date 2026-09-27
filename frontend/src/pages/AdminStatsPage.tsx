/**
 * Админка → Статистика: /admin/stats
 *
 * Главный вопрос этой страницы — не «сколько работ сдали», а «каких умений
 * не хватает школе»: блок слабых умений показывает, у какого учителя,
 * по какому предмету и в каком классе провал.
 */

import { useCallback, useEffect, useState } from 'react'
import { fetchSchoolStats } from '../api'
import type { SchoolStats } from '../types'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; stats: SchoolStats }
  | { kind: 'error'; message: string }

/** Периоды фильтра: 0 — за всё время. */
const PERIODS = [
  { days: 7, label: 'Неделя' },
  { days: 30, label: 'Месяц' },
  { days: 0, label: 'Всё время' },
]

/** Цвет по проценту — те же пороги, что на странице результатов. */
function levelClass(percent: number): string {
  if (percent < 50) {
    return 'cellval cellval--low'
  }
  if (percent <= 65) {
    return 'cellval cellval--mid'
  }
  return 'cellval cellval--high'
}

function formatDay(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
}

export default function AdminStatsPage() {
  const [days, setDays] = useState(30)
  const [subject, setSubject] = useState('')
  const [studentClass, setStudentClass] = useState('')
  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })

  // «Загружаем» ставят обработчики фильтров: внутри эффекта менять состояние
  // синхронно не нужно — при первом показе оно уже 'loading'.
  const load = useCallback(() => {
    fetchSchoolStats({ days, subject, studentClass })
      .then((stats) => setLoading({ kind: 'ready', stats }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить статистику',
        }),
      )
  }, [days, subject, studentClass])

  useEffect(load, [load])

  if (loading.kind === 'error') {
    return (
      <main className="page page--wide">
        <h1>Статистика</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
      </main>
    )
  }

  const stats = loading.kind === 'ready' ? loading.stats : null
  // Для графика берём самое загруженное число работ за день.
  const maxPerDay = Math.max(1, ...(stats?.by_day.map((d) => d.attempts_count) ?? [1]))

  return (
    <main className="page page--wide">
      <h1>Статистика школы</h1>
      <p className="lead">Считается по сданным работам. Фильтры действуют на все разделы.</p>

      {/* ------------------------- Фильтры ------------------------- */}
      <section className="card">
        <h2>Фильтры</h2>

        <div className="row row--tight">
          {PERIODS.map((period) => (
            <button
              key={period.days}
              type="button"
              className={days === period.days ? 'chip chip--active' : 'chip'}
              onClick={() => {
                setLoading({ kind: 'loading' })
                setDays(period.days)
              }}
            >
              {period.label}
            </button>
          ))}
        </div>

        <div className="fields fields--inline">
          <div className="field">
            <label className="label" htmlFor="filter-subject">
              Предмет
            </label>
            <select
              id="filter-subject"
              className="input select"
              value={subject}
              onChange={(event) => {
                setLoading({ kind: 'loading' })
                setSubject(event.target.value)
              }}
            >
              <option value="">Все предметы</option>
              {stats?.filters.subjects.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="label" htmlFor="filter-class">
              Класс
            </label>
            <select
              id="filter-class"
              className="input select"
              value={studentClass}
              onChange={(event) => {
                setLoading({ kind: 'loading' })
                setStudentClass(event.target.value)
              }}
            >
              <option value="">Все классы</option>
              {stats?.filters.classes.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {loading.kind === 'loading' && <p className="loading">Считаем…</p>}

      {stats !== null && (
        <>
          {/* ------------------------- Сводка ------------------------- */}
          <section className="card">
            <h2>Сводка</h2>
            <ul className="tiles">
              <li className="tile">
                <span className="tile__value">{stats.totals.teachers}</span>
                <span className="tile__label">учителей</span>
              </li>
              <li className="tile">
                <span className="tile__value">{stats.totals.tests}</span>
                <span className="tile__label">контрольных</span>
              </li>
              <li className="tile">
                <span className="tile__value">{stats.totals.attempts_week}</span>
                <span className="tile__label">работ за неделю</span>
              </li>
              <li className="tile">
                <span className="tile__value">{stats.totals.attempts_month}</span>
                <span className="tile__label">работ за месяц</span>
              </li>
              <li className="tile">
                <span className="tile__value">{stats.totals.attempts_total}</span>
                <span className="tile__label">работ всего</span>
              </li>
            </ul>
          </section>

          {/* ------------------------- Слабые умения ------------------------- */}
          <section className="card">
            <h2>Умения, которые не сформированы</h2>
            <p className="muted">
              Сверху — самые слабые. Красным отмечено всё, что ниже{' '}
              {stats.filters.weak_below}%.
            </p>

            {stats.weak_skills.length === 0 ? (
              <p className="empty">Нет данных: за выбранный период работ не сдавали.</p>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>%</th>
                      <th>Умение</th>
                      <th>Предмет</th>
                      <th>Класс</th>
                      <th>Учитель</th>
                      <th>Контрольная</th>
                      <th>Ответов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.weak_skills.map((skill, index) => (
                      <tr key={`${skill.skill_id}-${skill.student_class}-${index}`}>
                        <td>
                          <span className={levelClass(skill.percent)}>{skill.percent}%</span>
                        </td>
                        <td>{skill.title}</td>
                        <td>{skill.subject || '—'}</td>
                        <td>{skill.student_class}</td>
                        <td>{skill.teacher_name}</td>
                        <td>{skill.test_title}</td>
                        <td>{skill.answers_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* ------------------------- Разрезы ------------------------- */}
          <section className="card">
            <h2>По учителям</h2>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Учитель</th>
                    <th>Контрольных</th>
                    <th>Работ</th>
                    <th>Средний %</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_teacher.map((teacher) => (
                    <tr key={teacher.id}>
                      <td>{teacher.full_name}</td>
                      <td>{teacher.tests_count}</td>
                      <td>{teacher.attempts_count}</td>
                      <td>
                        <span className={levelClass(teacher.average_percent)}>
                          {teacher.average_percent}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>По предметам и классам</h2>

            <div className="twocol">
              <div>
                <h3>Предметы</h3>
                {stats.by_subject.length === 0 ? (
                  <p className="empty">Нет данных.</p>
                ) : (
                  <table className="table">
                    <tbody>
                      {stats.by_subject.map((row) => (
                        <tr key={row.subject}>
                          <td>{row.subject}</td>
                          <td>{row.attempts_count} работ</td>
                          <td>
                            <span className={levelClass(row.average_percent)}>
                              {row.average_percent}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <div>
                <h3>Классы</h3>
                {stats.by_class.length === 0 ? (
                  <p className="empty">Нет данных.</p>
                ) : (
                  <table className="table">
                    <tbody>
                      {stats.by_class.map((row) => (
                        <tr key={row.student_class}>
                          <td>{row.student_class}</td>
                          <td>{row.attempts_count} работ</td>
                          <td>
                            <span className={levelClass(row.average_percent)}>
                              {row.average_percent}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </section>

          {/* ------------------------- Активность ------------------------- */}
          <section className="card">
            <h2>Активность по дням</h2>
            {stats.by_day.length === 0 ? (
              <p className="empty">За выбранный период работ не сдавали.</p>
            ) : (
              <ul className="chart">
                {stats.by_day.map((day) => (
                  <li key={day.day} className="chart__item">
                    {/* Столбик — доля от самого загруженного дня. Число рядом
                        обязательно: по одной высоте точное значение не прочитать. */}
                    <span className="chart__count">{day.attempts_count}</span>
                    <span
                      className="chart__bar"
                      style={{ height: `${(day.attempts_count * 100) / maxPerDay}%` }}
                      aria-hidden="true"
                    />
                    <span className="chart__day">{formatDay(day.day)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </main>
  )
}
