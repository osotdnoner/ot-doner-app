/** @OnlyCurrentDoc */
/**
 * ОТ Донер — зарплатная таблица.
 *
 * Этот скрипт живёт внутри Google Таблицы. Он:
 *  1) один раз строит листы «Итоги», «Смены», «Ставки», «Как пользоваться» с формулами (функция setup);
 *  2) принимает данные из приложения и записывает их на лист «Смены» (функция doPost).
 *
 * Одна строка на листе «Смены» = один человек в одной смене: филиал, смена (день, ночь или полная),
 * пришёл вовремя / опоздал / не вышел, причина, штраф, бонус. Если админ поправил отметку или шеф
 * снял штраф, строка обновляется. Если сотрудника переотметили как «Выходной», строка удаляется.
 * Ставку, сумму к выплате и итоги за 1–15 и 16–конец месяца считают формулы.
 *
 * Подключение (один раз):
 *  1. Таблица → Расширения → Apps Script → вставить этот код целиком.
 *  2. В строке SECRET вписать секретное слово (то же — на сервере в GOOGLE_SHEET_SECRET).
 *  3. Сохранить, выбрать функцию setup → «Выполнить» → разрешить доступ.
 *  4. «Развертывание» → «Новое развертывание» → «Веб-приложение»:
 *     выполнять от имени «Я», доступ «Все» → «Развернуть».
 *  5. «URL веб-приложения» (…/exec) вписать на сервере в GOOGLE_SHEET_URL.
 */

const SECRET = "ВПИШИТЕ_СЕКРЕТНОЕ_СЛОВО";

const DATA = "Смены";
const RATES = "Ставки";
const TOTALS = "Итоги";
const HELP = "Как пользоваться";

// Колонки, которые присылает приложение (A–P). Порядок совпадает с payroll/sheets.py.
const HEADERS = [
  "Ключ", "Дата", "Филиал", "Смена", "Сотрудник", "Должность", "Выход", "Пришёл в", "Опоздание, мин",
  "Причина", "Штраф, ₸", "Бонус, ₸", "За что бонус", "Комментарий к штрафу", "Администратор смены", "Обновлено",
];

// Филиалы и должности — для листа «Ставки». Новые сочетания скрипт добавит сам, когда придут данные.
const BRANCHES = [
  { name: "27 мкрн", night: true, positions: ["Донерщик", "Пиццерист", "Кассир", "Раннер доставки", "Раннер кассы", "Техперсонал"] },
  { name: "16 мкрн", night: false, positions: ["Донерщик", "Кассир"] },
  { name: "17 мкрн", night: true, positions: ["Донерщик", "Пиццерист", "Кассир", "Раннер кассы", "Техперсонал кухни", "Техперсонал зала"] },
  { name: "14 мкрн", night: true, positions: ["Донерщик", "Пиццерист", "Кассир", "Раннер кассы", "Техперсонал"] },
];

const MONEY = "#,##0";
const INPUT_BG = "#fff6d6";
const HEAD_BG = "#c2410c";

// ---------------------------------------------------------------- приём данных

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000); // две отправки одновременно не перепутают строки
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) {
      return reply({ ok: false, error: "Секретное слово не совпадает: проверьте GOOGLE_SHEET_SECRET и SECRET в скрипте." });
    }
    const book = SpreadsheetApp.getActiveSpreadsheet();
    if (!book.getSheetByName(DATA)) setup();
    const sheet = book.getSheetByName(DATA);
    const headers = data.headers || HEADERS;
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

    // 1. Удаления («Выходной»): снизу вверх, чтобы номера строк не сдвигались
    const removed = (data.delete || []).length ? removeRows(sheet, data.delete) : 0;

    // 2. Новые и изменённые строки
    let keys = readKeys(sheet);
    let added = 0;
    let updated = 0;
    (data.rows || []).forEach((row) => {
      const values = row.slice();
      values[1] = toSerialDate(values[1]);
      const index = keys.indexOf(String(values[0]));
      let r;
      if (index >= 0) {
        r = index + 2;
        updated++;
      } else {
        r = keys.length + 2;
        if (r > sheet.getMaxRows()) sheet.insertRowsAfter(sheet.getMaxRows(), 200);
        keys.push(String(values[0]));
        added++;
      }
      sheet.getRange(r, 1, 1, values.length).setValues([values]);
      sheet.getRange(r, 2).setNumberFormat("dd.mm.yyyy");
    });

    addMissingRates(book, data.rows || []);
    return reply({ ok: true, added: added, updated: updated, removed: removed });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return reply({ ok: true, message: "Скрипт ОТ Донер работает. Данные приходят на лист «" + DATA + "»." });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function readKeys(sheet) {
  const last = sheet.getLastRow();
  return last > 1 ? sheet.getRange(2, 1, last - 1, 1).getValues().map((r) => String(r[0])) : [];
}

function removeRows(sheet, keysToDelete) {
  const keys = readKeys(sheet);
  const rows = [];
  keysToDelete.forEach((key) => {
    const index = keys.indexOf(String(key));
    if (index >= 0) rows.push(index + 2);
  });
  rows.sort((a, b) => b - a).forEach((r) => sheet.deleteRow(r));
  return rows.length;
}

/** «2026-10-16» → число-дата Google (без сдвига из-за часовых поясов). */
function toSerialDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
  if (!m) return value;
  return (Date.UTC(+m[1], +m[2] - 1, +m[3]) - Date.UTC(1899, 11, 30)) / 86400000;
}

function addMissingRates(book, rows) {
  const sheet = book.getSheetByName(RATES);
  if (!sheet || !rows.length) return;
  const last = sheet.getLastRow();
  const existing = last > 1
    ? sheet.getRange(2, 2, last - 1, 2).getValues().map((r) => r[0] + "|" + r[1])
    : [];
  rows.forEach((row) => {
    const key = row[2] + "|" + row[5];
    if (existing.indexOf(key) >= 0) return;
    existing.push(key);
    const r = sheet.getLastRow() + 1;
    sheet.getRange(r, 2, 1, 2).setValues([[row[2], row[5]]]);
    sheet.getRange(r, 6).setFormula(F("=D" + r + "+E" + r));
    sheet.getRange(r, 4, 1, 3).setBackground(INPUT_BG).setNumberFormat(MONEY);
  });
}

// ---------------------------------------------------------------- формулы под язык таблицы

// В русской таблице аргументы формул разделяются «;», в английской — «,». Скрипт пишет формулы
// с запятыми и сам переводит их под язык таблицы (запятые внутри кавычек не трогает).
let SEPARATOR = null;

function separator() {
  if (SEPARATOR) return SEPARATOR;
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const probe = book.insertSheet("проверка_" + Date.now());
  probe.getRange("A1").setFormula("=SUM(1,2)");
  SpreadsheetApp.flush();
  SEPARATOR = probe.getRange("A1").getDisplayValue() === "3" ? "," : ";";
  book.deleteSheet(probe);
  return SEPARATOR;
}

function F(formula) {
  if (separator() === ",") return formula;
  let out = "";
  let inString = false;
  for (const ch of formula) {
    if (ch === '"') inString = !inString;
    out += !inString && ch === "," ? ";" : ch;
  }
  return out;
}

/** Перестроить листы заново (только пока на листе «Смены» нет данных). */
function rebuild() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const data = book.getSheetByName(DATA);
  if (data && data.getLastRow() > 1) throw new Error("На листе «Смены» уже есть данные — перестраивать нельзя.");
  const keep = book.insertSheet("временный");
  [TOTALS, DATA, RATES, HELP].forEach((name) => {
    const s = book.getSheetByName(name);
    if (s) book.deleteSheet(s);
  });
  setup();
  book.deleteSheet(keep);
}

// ---------------------------------------------------------------- построение таблицы

function setup() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.setSpreadsheetTimeZone("Asia/Almaty");
  buildRates(book);
  buildData(book);
  buildTotals(book);
  buildHelp(book);
  ["Лист1", "Sheet1", "Лист 1", "временный"].forEach((name) => {
    const s = book.getSheetByName(name);
    if (s && s.getLastRow() === 0 && book.getSheets().length > 1) book.deleteSheet(s);
  });
  book.setActiveSheet(book.getSheetByName(TOTALS));
  book.moveActiveSheet(1);
}

function sheetNamed(book, name) {
  return book.getSheetByName(name) || book.insertSheet(name);
}

function styleHeader(range) {
  range.setFontWeight("bold").setBackground(HEAD_BG).setFontColor("#ffffff").setVerticalAlignment("middle").setWrap(true);
}

function buildData(book) {
  const sheet = sheetNamed(book, DATA);
  if (sheet.getLastRow() > 0) return; // уже построен — данные не трогаем
  sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  const key = "C2:C&\"|\"&F2:F";
  const look = (col) => "VLOOKUP(" + key + ",'" + RATES + "'!A:F," + col + ",0)";
  sheet.getRange("Q1").setFormula(F(
    '={"Ставка, ₸";ARRAYFORMULA(IF(A2:A="",,IF(G2:G="Не вышел",0,IFERROR(' +
    'IF(D2:D="День",' + look(4) + ',IF(D2:D="Ночь",' + look(5) + ',' + look(6) + '))*1,0))))}'
  ));
  sheet.getRange("R1").setFormula(F('={"К выплате, ₸";ARRAYFORMULA(IF(A2:A="",,Q2:Q-K2:K+L2:L))}'));
  sheet.getRange("S1").setFormula(F('={"Отработано смен";ARRAYFORMULA(IF(A2:A="",,IF(G2:G="Не вышел",0,1)))}'));
  sheet.getRange("T1").setFormula(F('={"Опозданий";ARRAYFORMULA(IF(A2:A="",,IF(G2:G="Опоздал",1,0)))}'));
  sheet.getRange("U1").setFormula(F('={"Невыходов";ARRAYFORMULA(IF(A2:A="",,IF(G2:G="Не вышел",1,0)))}'));

  styleHeader(sheet.getRange("A1:U1"));
  sheet.getRange("Q1:U1").setBackground("#7c2d12");
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(1);
  sheet.hideColumns(1); // ключ нужен только скрипту
  sheet.getRange("B2:B").setNumberFormat("dd.mm.yyyy");
  sheet.getRange("K2:L").setNumberFormat(MONEY);
  sheet.getRange("Q2:R").setNumberFormat(MONEY);
  const widths = { 2: 90, 3: 80, 4: 70, 5: 170, 6: 130, 7: 120, 8: 70, 9: 85, 10: 220, 11: 80, 12: 80, 13: 180,
    14: 180, 15: 130, 16: 120, 17: 85, 18: 95, 19: 85, 20: 80, 21: 80 };
  Object.keys(widths).forEach((c) => sheet.setColumnWidth(+c, widths[c]));
  sheet.setRowHeight(1, 42);

  const rules = [
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=$G2="Не вышел"').setBackground("#fdecea")
      .setRanges([sheet.getRange("B2:U")]).build(),
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=$G2="Опоздал"').setBackground("#fff6e0")
      .setRanges([sheet.getRange("B2:U")]).build(),
  ];
  sheet.setConditionalFormatRules(rules);
  sheet.getRange("A1:U").createFilter();
  sheet.protect().setDescription("Заполняет приложение ОТ Донер").setWarningOnly(true);
}

function buildRates(book) {
  const sheet = sheetNamed(book, RATES);
  if (sheet.getLastRow() > 0) return;
  sheet.getRange("A1").setFormula(F('={"Ключ";ARRAYFORMULA(IF(B2:B="",,B2:B&"|"&C2:C))}'));
  sheet.getRange("B1:F1").setValues([["Филиал", "Должность", "День, ₸", "Ночь, ₸", "Полная смена, ₸"]]);
  const rows = [];
  BRANCHES.forEach((b) => {
    rows.push([b.name, "Администратор", b.night]);
    b.positions.forEach((p) => rows.push([b.name, p, b.night]));
  });
  rows.forEach((row, i) => {
    const r = i + 2;
    sheet.getRange(r, 2, 1, 2).setValues([[row[0], row[1]]]);
    sheet.getRange(r, 6).setFormula(F(row[2] ? "=D" + r + "+E" + r : "=D" + r));
    sheet.getRange(r, 4, 1, 3).setNumberFormat(MONEY);
    sheet.getRange(r, 4).setBackground(INPUT_BG);
    if (row[2]) {
      sheet.getRange(r, 5).setBackground(INPUT_BG);
    } else {
      sheet.getRange(r, 5).setValue("нет ночи").setFontColor("#9a8f86");
    }
    sheet.getRange(r, 6).setBackground("#fdf2e2");
  });
  styleHeader(sheet.getRange("A1:F1"));
  sheet.setFrozenRows(1);
  sheet.hideColumns(1);
  sheet.setColumnWidth(2, 90);
  sheet.setColumnWidth(3, 160);
  sheet.setColumnWidths(4, 3, 120);
  sheet.getRange("D1").setNote("Сколько платим за одну дневную смену. Впишите сумму в жёлтые клетки.");
  sheet.getRange("E1").setNote("Сколько платим за одну ночную смену.");
  sheet.getRange("F1").setNote("Полная смена (день + ночь, когда ночного персонала нет). " +
    "По умолчанию = день + ночь. Если платите иначе — просто впишите свою сумму поверх формулы.");
}

function buildTotals(book) {
  const sheet = sheetNamed(book, TOTALS);
  if (sheet.getLastRow() > 0) return;
  sheet.getRange("A1:A4").setValues([["Месяц"], ["Период"], ["С"], ["По"]]).setFontWeight("bold");
  sheet.getRange("B1").setFormula(F("=EOMONTH(TODAY(),-1)+1")).setNumberFormat("mmmm yyyy").setBackground(INPUT_BG)
    .setNote("Любая дата нужного месяца. Сейчас стоит текущий месяц — впишите, например, 01.09.2026, чтобы посмотреть сентябрь.");
  sheet.getRange("B2").setValue("1–15").setBackground(INPUT_BG).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(["1–15", "16–конец месяца", "Весь месяц"], true).build()
  );
  sheet.getRange("B3").setFormula(F('=IF(B2="16–конец месяца",DATE(YEAR(B1),MONTH(B1),16),DATE(YEAR(B1),MONTH(B1),1))'));
  sheet.getRange("B4").setFormula(F('=IF(B2="1–15",DATE(YEAR(B1),MONTH(B1),15),EOMONTH(B1,0))'));
  sheet.getRange("B3:B4").setNumberFormat("dd.mm.yyyy");

  // К выплате по филиалам
  sheet.getRange("D1:E1").setValues([["Филиал", "К выплате, ₸"]]);
  styleHeader(sheet.getRange("D1:E1"));
  BRANCHES.forEach((b, i) => {
    const r = i + 2;
    sheet.getRange(r, 4).setValue(b.name);
    sheet.getRange(r, 5).setFormula(F(
      "=SUMIFS('" + DATA + "'!R:R,'" + DATA + "'!C:C,D" + r + ",'" + DATA + "'!B:B,\">=\"&$B$3,'" + DATA + "'!B:B,\"<=\"&$B$4)"
    ));
  });
  const totalRow = BRANCHES.length + 2;
  sheet.getRange(totalRow, 4).setValue("Всего").setFontWeight("bold");
  sheet.getRange(totalRow, 5).setFormula(F(
    "=SUMIFS('" + DATA + "'!R:R,'" + DATA + "'!B:B,\">=\"&$B$3,'" + DATA + "'!B:B,\"<=\"&$B$4)"
  )).setFontWeight("bold");
  sheet.getRange(2, 5, BRANCHES.length + 1, 1).setNumberFormat(MONEY);

  // По каждому сотруднику за период
  const start = totalRow + 2;
  sheet.getRange(start, 1).setValue("По сотрудникам за период").setFontWeight("bold").setFontSize(12);
  const q = "select C, E, F, sum(S), sum(T), sum(U), sum(Q), sum(K), sum(L), sum(R) " +
    "where A is not null and B >= date '\"&TEXT($B$3,\"yyyy-mm-dd\")&\"' and B <= date '\"&TEXT($B$4,\"yyyy-mm-dd\")&\"' " +
    "group by C, E, F order by C, E " +
    "label C 'Филиал', E 'Сотрудник', F 'Должность', sum(S) 'Смен', sum(T) 'Опозданий', sum(U) 'Невыходов', " +
    "sum(Q) 'По ставкам, ₸', sum(K) 'Штрафы, ₸', sum(L) 'Бонусы, ₸', sum(R) 'К выплате, ₸'";
  sheet.getRange(start + 1, 1).setFormula(F(
    "=IFERROR(QUERY('" + DATA + "'!A:U,\"" + q + "\",1),\"За этот период отметок пока нет\")"
  ));
  styleHeader(sheet.getRange(start + 1, 1, 1, 10));
  sheet.getRange(start + 2, 7, 500, 4).setNumberFormat(MONEY);
  sheet.setFrozenRows(0);
  sheet.setColumnWidth(1, 110);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 130);
  sheet.setColumnWidth(4, 110);
  sheet.setColumnWidths(5, 6, 105);
}

function buildHelp(book) {
  const sheet = sheetNamed(book, HELP);
  if (sheet.getLastRow() > 0) return;
  const lines = [
    ["Зарплатная таблица «ОТ Донер»"],
    [""],
    ["«Итоги» — сколько платить каждому за период. Вверху выберите месяц и период: 1–15, 16–конец месяца или весь месяц."],
    ["«Смены» — каждую строку заполняет приложение: кто вышел, на какую смену, опоздание, причина, штраф, бонус."],
    ["   Руками здесь ничего менять не нужно: изменения делаются в приложении (шеф — в панели «Зарплата → Выходы на смену»)."],
    ["«Ставки» — сколько платим за дневную, ночную и полную смену по каждой должности. Заполните жёлтые клетки."],
    ["   Полная смена по умолчанию = день + ночь. Если платите иначе, впишите свою сумму поверх формулы."],
    [""],
    ["Как считается: К выплате за смену = ставка − штраф + бонус. Если сотрудник не вышел, ставка 0, штраф — если назначен."],
    ["Штрафы за опоздание считает приложение по ступеням из панели «Зарплата → Правила штрафов»."],
  ];
  sheet.getRange(1, 1, lines.length, 1).setValues(lines);
  sheet.getRange("A1").setFontWeight("bold").setFontSize(14);
  sheet.setColumnWidth(1, 900);
}
