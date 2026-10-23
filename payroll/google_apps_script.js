/** @OnlyCurrentDoc */
/**
 * ОТ Донер — зарплатная таблица.
 *
 * Этот скрипт живёт внутри Google Таблицы. Он:
 *  1) строит листы «Итоги», «Ставки», «Журнал», «Как пользоваться» с формулами (функция setup);
 *  2) принимает данные из приложения и записывает их на лист «Журнал» (функция doPost).
 *
 * Строки «Журнала» двух видов:
 *  • «Смена» — один человек в одной смене: филиал, смена (день, ночь или полная), выход, опоздание,
 *    штрафы, бонусы, замена (вместо кого, с какого филиала или из резерва);
 *  • «Аванс» — деньги, выданные заранее.
 * Если админ поправил отметку или шеф снял штраф, строка обновляется. «Выходной», удалённая замена
 * или удалённый аванс — строка удаляется. Ставку, начислено, к выплате и итоги считают формулы.
 *
 * Если скрипт обновили (новая раскладка), выполните rebuild: листы перестроятся (ставки сохранятся),
 * а приложение отправит все данные заново.
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

const DATA = "Журнал";
const OLD_DATA = ["Смены"]; // старые названия листа с данными
const RATES = "Ставки";
const TOTALS = "Итоги";
const HELP = "Как пользоваться";

// Колонки, которые присылает приложение (A–U). Порядок совпадает с payroll/sheets.py.
const HEADERS = [
  "Ключ", "Дата", "Филиал", "Что", "Смена", "Сотрудник", "Родной филиал", "Должность", "Выход", "Пришёл в",
  "Опоздание, мин", "Причина", "Штраф, ₸", "За что штраф", "Бонус, ₸", "За что бонус", "Аванс, ₸", "Замена",
  "Комментарий", "Кто отметил", "Обновлено",
];
// Что считают формулы (V–AA)
const COMPUTED = ["Ставка, ₸", "Начислено, ₸", "К выплате, ₸", "Смен", "Опозданий", "Невыходов"];

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

    // 1. Удаления («Выходной», удалённые замена или аванс): снизу вверх, чтобы номера строк не сдвигались
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
    if (row[3] !== "Смена") return; // ставка нужна только сменам, не авансам
    const key = row[2] + "|" + row[7];
    if (existing.indexOf(key) >= 0) return;
    existing.push(key);
    const r = sheet.getLastRow() + 1;
    sheet.getRange(r, 2, 1, 2).setValues([[row[2], row[7]]]);
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

/**
 * Перестроить листы под новую версию скрипта. Ставки шефа сохраняются, а журнал и итоги
 * строятся заново — приложение отправит все отметки и авансы ещё раз (команда sync_sheet).
 */
function rebuild() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  book.insertSheet("временный"); // в таблице всегда должен оставаться хотя бы один лист
  [TOTALS, DATA, HELP].concat(OLD_DATA).forEach((name) => {
    const s = book.getSheetByName(name);
    if (s) book.deleteSheet(s);
  });
  setup(); // setup сам уберёт временный лист
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
  [TOTALS, RATES, DATA, HELP].forEach((name, i) => {
    book.setActiveSheet(book.getSheetByName(name));
    book.moveActiveSheet(i + 1);
  });
  book.setActiveSheet(book.getSheetByName(TOTALS));
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
  // Ставка: по филиалу, где работал (C), должности на смене (H) и виду смены (E). Авансам и невыходам — 0.
  const key = "C2:C&\"|\"&H2:H";
  const look = (col) => "VLOOKUP(" + key + ",'" + RATES + "'!A:F," + col + ",0)";
  sheet.getRange("V1").setFormula(F(
    '={"Ставка, ₸";ARRAYFORMULA(IF(A2:A="",,IF((D2:D<>"Смена")+(I2:I="Не вышел"),0,IFERROR(' +
    'IF(E2:E="День",' + look(4) + ',IF(E2:E="Ночь",' + look(5) + ',' + look(6) + '))*1,0))))}'
  ));
  sheet.getRange("W1").setFormula(F('={"Начислено, ₸";ARRAYFORMULA(IF(A2:A="",,V2:V-M2:M+O2:O))}'));
  sheet.getRange("X1").setFormula(F('={"К выплате, ₸";ARRAYFORMULA(IF(A2:A="",,W2:W-Q2:Q))}'));
  sheet.getRange("Y1").setFormula(F('={"Смен";ARRAYFORMULA(IF(A2:A="",,IF((D2:D="Смена")*(I2:I<>"Не вышел"),1,0)))}'));
  sheet.getRange("Z1").setFormula(F('={"Опозданий";ARRAYFORMULA(IF(A2:A="",,IF(I2:I="Опоздал",1,0)))}'));
  sheet.getRange("AA1").setFormula(F('={"Невыходов";ARRAYFORMULA(IF(A2:A="",,IF(I2:I="Не вышел",1,0)))}'));

  styleHeader(sheet.getRange("A1:AA1"));
  sheet.getRange("V1:AA1").setBackground("#7c2d12");
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(1);
  sheet.hideColumns(1); // ключ нужен только скрипту
  sheet.getRange("B2:B").setNumberFormat("dd.mm.yyyy");
  ["M2:M", "O2:O", "Q2:Q", "V2:X"].forEach((a1) => sheet.getRange(a1).setNumberFormat(MONEY));
  const widths = { 2: 85, 3: 80, 4: 60, 5: 65, 6: 160, 7: 90, 8: 120, 9: 115, 10: 65, 11: 80, 12: 170, 13: 75,
    14: 200, 15: 75, 16: 170, 17: 80, 18: 220, 19: 160, 20: 110, 21: 120, 22: 80, 23: 95, 24: 95, 25: 60, 26: 75, 27: 80 };
  Object.keys(widths).forEach((c) => sheet.setColumnWidth(+c, widths[c]));
  sheet.setRowHeight(1, 42);

  const rule = (formula, color) => SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied(formula)
    .setBackground(color).setRanges([sheet.getRange("B2:AA")]).build();
  sheet.setConditionalFormatRules([
    rule('=$I2="Не вышел"', "#fdecea"),
    rule('=$I2="Опоздал"', "#fff6e0"),
    rule('=$D2="Аванс"', "#e8f0fe"),
    rule('=$R2<>""', "#eaf7ee"),
  ]);
  sheet.getRange("A1:AA").createFilter();
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

  // По филиалам: сколько начислено за работу на филиале и сколько авансов там выдано
  sheet.getRange("D1:F1").setValues([["Филиал", "Начислено, ₸", "Авансы, ₸"]]);
  styleHeader(sheet.getRange("D1:F1"));
  const period = ",'" + DATA + "'!B:B,\">=\"&$B$3,'" + DATA + "'!B:B,\"<=\"&$B$4)";
  BRANCHES.forEach((b, i) => {
    const r = i + 2;
    sheet.getRange(r, 4).setValue(b.name);
    sheet.getRange(r, 5).setFormula(F("=SUMIFS('" + DATA + "'!W:W,'" + DATA + "'!C:C,D" + r + period));
    sheet.getRange(r, 6).setFormula(F("=SUMIFS('" + DATA + "'!Q:Q,'" + DATA + "'!C:C,D" + r + period));
  });
  const totalRow = BRANCHES.length + 2;
  sheet.getRange(totalRow, 4).setValue("Всего").setFontWeight("bold");
  sheet.getRange(totalRow, 5).setFormula(F("=SUMIFS('" + DATA + "'!W:W" + period)).setFontWeight("bold");
  sheet.getRange(totalRow, 6).setFormula(F("=SUMIFS('" + DATA + "'!Q:Q" + period)).setFontWeight("bold");
  sheet.getRange(2, 5, BRANCHES.length + 1, 2).setNumberFormat(MONEY);

  // По каждому человеку за период: все его смены (и замены на других филиалах) и авансы
  const start = totalRow + 2;
  sheet.getRange(start, 1).setValue("По сотрудникам за период").setFontWeight("bold").setFontSize(12);
  const q = "select G, F, sum(Y), sum(Z), sum(AA), sum(V), sum(O), sum(M), sum(W), sum(Q), sum(X) " +
    "where A is not null and B >= date '\"&TEXT($B$3,\"yyyy-mm-dd\")&\"' and B <= date '\"&TEXT($B$4,\"yyyy-mm-dd\")&\"' " +
    "group by G, F order by G, F " +
    "label G 'Родной филиал', F 'Сотрудник', sum(Y) 'Смен', sum(Z) 'Опозданий', sum(AA) 'Невыходов', " +
    "sum(V) 'По ставкам, ₸', sum(O) 'Бонусы, ₸', sum(M) 'Штрафы, ₸', sum(W) 'Начислено, ₸', " +
    "sum(Q) 'Авансы, ₸', sum(X) 'К выплате, ₸'";
  sheet.getRange(start + 1, 1).setFormula(F(
    "=IFERROR(QUERY('" + DATA + "'!A:AA,\"" + q + "\",1),\"За этот период отметок пока нет\")"
  ));
  styleHeader(sheet.getRange(start + 1, 1, 1, 11));
  sheet.getRange(start + 2, 6, 500, 6).setNumberFormat(MONEY);
  sheet.setColumnWidth(1, 110);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 130);
  sheet.setColumnWidth(4, 110);
  sheet.setColumnWidths(5, 7, 105);
}

function buildHelp(book) {
  const sheet = sheetNamed(book, HELP);
  if (sheet.getLastRow() > 0) return;
  const lines = [
    ["Зарплатная таблица «ОТ Донер»"],
    [""],
    ["«Итоги» — сколько платить каждому за период. Вверху выберите месяц и период: 1–15, 16–конец месяца или весь месяц."],
    ["   По каждому человеку: смены (включая замены на других филиалах), опоздания, невыходы, ставки, бонусы, штрафы, авансы."],
    ["«Ставки» — сколько платим за дневную, ночную и полную смену по каждой должности. Заполните жёлтые клетки."],
    ["   Полная смена по умолчанию = день + ночь. Если платите иначе, впишите свою сумму поверх формулы."],
    ["«Журнал» — каждую строку заполняет приложение. Руками здесь ничего менять не нужно:"],
    ["   изменения делаются в приложении (шеф — в панели «Зарплата»)."],
    ["   Строка «Смена» — кто вышел, на какую смену, опоздание, штрафы, бонусы, замена. Строка «Аванс» — выданные деньги."],
    ["   Цвета: красный — не вышел, жёлтый — опоздал, зелёный — замена, голубой — аванс."],
    [""],
    ["Как считается: Начислено за смену = ставка − штрафы + бонусы. К выплате = начислено − авансы."],
    ["Замена получает ставку того филиала и должности, где работала. Если сотрудник не вышел, ставка 0."],
    ["Штрафы за опоздание считает приложение по ступеням из панели «Зарплата → Правила штрафов»."],
  ];
  sheet.getRange(1, 1, lines.length, 1).setValues(lines);
  sheet.getRange("A1").setFontWeight("bold").setFontSize(14);
  sheet.setColumnWidth(1, 900);
}
