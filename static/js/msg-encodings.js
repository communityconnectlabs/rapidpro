/*jshint esversion: 9 */
/*jshint strict:false */

var GSM7_REPLACEMENTS = ""; // loaded later in the file where code called (e.g campaign_event.haml)
const GSM7_BASIC = '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ`¿abcdefghijklmnopqrstuvwxyzäöñüà§';
const GSM7_EXTENDED = '^{}\\[~]|€';

const GSM7_BASIC_CHARS = GSM7_BASIC.split('').reduce(
  (acc, key) => ({...acc, [key]: key}), {}
);
const GSM7_EXTENDED_CHARS = GSM7_EXTENDED.split('').reduce(
  (acc, key) => ({...acc, [key]: key}),
  {}
);
const GSM7_CHARS = {...GSM7_BASIC_CHARS, ...GSM7_EXTENDED_CHARS};

const getHexFromChar = (char) =>
  String(char)
    .charCodeAt(0)
    .toString(16)
    .padStart(4, '0')
    .toUpperCase();

const isGSMText = (text) => {
  const textList = text.split('');
  let i = textList.length;
  while (i--) {
    const char = text[i];
    if (GSM7_CHARS[char] === undefined) {
      return false;
    }
  }

  return true;
};

const GSM7_SINGLE_SEGMENT = 160;
const GSM7_MULTI_SEGMENTS = 153;
const UCS2_SINGLE_SEGMENT = 70;
const UCS2_MULTI_SEGMENTS = 67;

const getMessageInfo = (text) => {
  const isGSM = isGSMText(text);
  let isMultipart = false;
  let segmentSize = 0;
  let accentedChars = new Set();

  const messageInfo = {
    isGSM,
    isMultipart: false,
    segmentCount: 1,
    characterSet: isGSM ? 'GSM/7-bit' : 'UCS-2',
    count: text.length
  };

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (isGSM && GSM7_EXTENDED_CHARS[char] !== undefined) {
      segmentSize += 2;
    } else {
      segmentSize += 1;
    }

    if (
      (!isGSM && segmentSize > UCS2_SINGLE_SEGMENT) ||
      (isGSM && segmentSize > GSM7_SINGLE_SEGMENT)
    ) {
      isMultipart = true;
      break;
    }
  }

  if (!isMultipart) {
    return {...messageInfo, accentedChars: Array.from(accentedChars)};
  }

  segmentSize = 0;
  let segmentCount = 1;
  let count = 0;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const charHex = getHexFromChar(char);
    if (GSM7_REPLACEMENTS[charHex] !== undefined) {
      accentedChars.add(char);
    }
    if (isGSM && GSM7_EXTENDED_CHARS[char] !== undefined) {
      segmentSize += 2;
      count += 2;
    } else {
      segmentSize += 1;
      count += 1;
    }

    if (isGSM && segmentSize > GSM7_MULTI_SEGMENTS) {
      segmentSize -= GSM7_MULTI_SEGMENTS;
      segmentCount++;
    }
    if (!isGSM && segmentSize > UCS2_MULTI_SEGMENTS) {
      segmentSize -= UCS2_MULTI_SEGMENTS;
      segmentCount++;
    }
  }
  return {
    ...messageInfo,
    count,
    isMultipart,
    segmentCount,
    accentedChars: Array.from(accentedChars)
  };
};

function toggleElementClass(trigger, el, styleClass) {
  if (trigger) {
    el.classList.add(styleClass);
  } else {
    el.classList.remove(styleClass);
  }
}

function renderCharReplaceDialog(info) {
  let body = document.createElement("div");
  let message = 'This message is UCS-2 encoded. UCS-2 has only 70 characters per segment vs. 160 characters\n' +
                'for 7-bit/GSM. If you replace the following characters, you can get more space for your\n' +
                'message and likely save money.';
  body.style.padding = "10px 20px";
  body.innerHTML = `<div>${message}<div class="pt-0">${info.accentedChars.join(', ')}</div></div>`;
  return body;
}

function renderCharReplaceResultDialog(info) {
  let body = document.createElement("div");
  let replacedMsg = "The following characters have been replaced";
  let removedMsg = "The following characters have been removed";
  let replacedChars = Object.entries(info.replaced).map(([key, value]) => `<div>${key} -> ${value}</div>`).join("\n");
  let removedChars = (info.removed || []).join(", ");
  let removedText = (info.removed || []).length > 0 ? `<div>${removedMsg}: ${removedChars}</div>`: "";
  body.style.padding = "10px 20px";
  body.innerHTML = `<div>${replacedMsg}<br/>${replacedChars}${removedText}</div>`;
  return body;
}

function renderCharReplaceErrorDialog() {
  let body = document.createElement("div");
  let errorMsg = "Sorry, the try to replace accented characters has failed.";
  body.style.padding = "10px 20px";
  body.innerHTML = `<div>${errorMsg}</div>`;
  return body;
}

function initReplaceCharDialog(fieldName, fieldContainer, replaceCharsUrl, csrftoken, hiddenFieldsToUpdateValue) {
  let replaceCharWindowID = `${fieldName}-replace-char`;
  let replaceCharWindow = document.createElement("temba-dialog");
  replaceCharWindow.id = replaceCharWindowID;
  replaceCharWindow.header = "Replace Accented Text";
  replaceCharWindow.width = "400px";

  // init functions
  let replaceCharsListener = function (evt) {
    if (!evt.detail.button.secondary) {
      replaceCharWindow.submitting = true;
      fetch(replaceCharsUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrftoken,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: fieldContainer.value
        }),
      }).then((response) => response.json()).then((data) => {
        replaceCharWindow.body = renderCharReplaceResultDialog(data);
        replaceCharWindow.submitting = false;
        replaceCharWindow.cancelButtonName = "OK";
        replaceCharWindow.primaryButtonName = "";
        fieldContainer.value = data.updated;
        hiddenFieldsToUpdateValue.forEach(el => {
          el.value = data.updated;
        });
      }).catch(() => {
        replaceCharWindow.body = renderCharReplaceErrorDialog();
        replaceCharWindow.submitting = false;
        replaceCharWindow.cancelButtonName = "Close";
        replaceCharWindow.primaryButtonName = "";
      });
      replaceCharWindow.removeEventListener("temba-button-clicked", replaceCharsListener);
      replaceCharWindow.addEventListener("temba-button-clicked", closeWindow);
    }
  };

  let closeWindow = function (evt) {
    if (!evt.detail.button.secondary && replaceCharWindow.submitting) {
      replaceCharWindow.submitting = false;
      return;
    }
    replaceCharWindow.removeEventListener("temba-button-clicked", closeWindow);
    replaceCharWindow.hide();
  };

  let openDialogFuncName = `${fieldName}ShowReplaceCharDialog`;
  window[openDialogFuncName] = function () {
    let info = getMessageInfo(fieldContainer.value);
    replaceCharWindow.body = renderCharReplaceDialog(info);
    replaceCharWindow.primaryButtonName = "Replace";
    replaceCharWindow.cancelButtonName = "Cancel";
    replaceCharWindow.shadowRoot.querySelector("#dialog-mask").children[1].style.height = "100vh";
    replaceCharWindow.addEventListener("temba-button-clicked", replaceCharsListener);
    replaceCharWindow.show();
  };

  // remove dialogs elements
  let existingReplaceCharWindow = document.getElementById(replaceCharWindowID);
  if (existingReplaceCharWindow) {
    existingReplaceCharWindow.remove();
  }
  // add just created
  document.body.append(replaceCharWindow);
  return replaceCharWindow;
}
