function getHTTPOptions() {
  return {
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'X-CSRFToken': getCookie('csrftoken')
    }
  };
}

function createErrorAlert(message) {
  const alertElem = document.createElement('div');
  alertElem.innerText = message;
  alertElem.classList.add('alert-error');
  return alertElem;
}

function getFieldFromElement(formElement) {
  return formElement
    .shadowRoot.querySelector('temba-field')
    .shadowRoot.querySelector('.field');
}

function addErrorAlert(formElement, message) {
  const alertElem = createErrorAlert(message);
  const field = getFieldFromElement(formElement);
  field.append(alertElem);
}

function removeErrorAlert(formElement) {
  const field = getFieldFromElement(formElement);
  const alertElem = field.querySelector('.alert-error');
  if (alertElem) field.removeChild(alertElem);
}

function createButton(options) {
  const buttonOptions = Object.prototype.toString.call(options) === '[object Object]' ? options : {};
  const buttonElement = document.createElement('temba-button');
  buttonElement.name = buttonOptions.name;
  if (options.onclick) buttonElement.onclick = options.onclick;
  if (options.primary) buttonElement.primary = true;
  else buttonElement.secondary = true;
  return buttonElement;
}

function getModalResources(modaxId) {
  const id = modaxId || 'create-flow-modal';
  const modalView = document.querySelector(`temba-modax#${id}`);
  const dialogWrapper =  modalView.shadowRoot.querySelector('temba-dialog');
  const dialogFooter = dialogWrapper.shadowRoot.querySelector('.dialog-footer');
  const dialogBody = dialogWrapper.shadowRoot.querySelector('.dialog-body');
  const modaxBody = modalView.shadowRoot.querySelector('temba-dialog').querySelector('.modax-body');
  return { modalView, dialogWrapper, dialogBody, dialogFooter, modaxBody };
}
