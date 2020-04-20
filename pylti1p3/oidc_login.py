import uuid
from abc import ABCMeta, abstractmethod
try:
    from urllib import urlencode
except ImportError:
    from urllib.parse import urlencode

from .actions import Action
from .cookies_allowed_check import CookiesAllowedCheckPage
from .exception import OIDCException


class OIDCLogin(object):
    __metaclass__ = ABCMeta
    _request = None
    _tool_config = None
    _session_service = None
    _cookie_service = None
    _launch_data_storage = None

    _browser_allows_cookies_check_enabled = False
    _cookies_check_loading_text = 'Loading...'
    _cookies_unavailable_msg_main_text = 'Your browser prohibits to save cookies in the iframes.'
    _cookies_unavailable_msg_click_text = 'Click here to open content in the new tab.'
    _state_params = {}

    def __init__(self, request, tool_config, session_service, cookie_service, launch_data_storage=None):
        self._request = request
        self._tool_config = tool_config
        self._session_service = session_service
        self._cookie_service = cookie_service
        self._launch_data_storage = launch_data_storage

    @abstractmethod
    def get_redirect(self, url):
        raise NotImplementedError

    def get_response(self, html):  # pylint: disable=unused-argument
        return ''

    def _get_request_param(self, key):
        return self._request.get_param(key)

    def _get_uuid(self):
        return str(uuid.uuid4())

    def _is_new_window_request(self):
        lti_new_window = self._get_request_param('lti1p3_new_window')
        return bool(lti_new_window)

    def _prepare_redirect_url(self, launch_url):
        if not launch_url:
            raise OIDCException("No launch URL configured")

        if self._launch_data_storage:
            self.set_launch_data_storage(self._launch_data_storage)

        # validate request
        registration = self.validate_oidc_login()

        # build OIDC Auth Response

        # generate state
        # set cookie (short lived)
        state = 'state-' + self._get_uuid()
        self._cookie_service.set_cookie(state, state, 5 * 60)  # 5 min

        # generate nonce
        nonce = self._get_uuid()
        self._session_service.save_nonce(nonce)
        if self._state_params:
            self._session_service.save_state_params(state, self._state_params)

        # build Response
        auth_params = {
            'scope': 'openid',  # OIDC Scope
            'response_type': 'id_token',  # OIDC response is always an id token
            'response_mode': 'form_post',  # OIDC response is always a form post
            'prompt': 'none',  # Don't prompt user on redirect
            'client_id': registration.get_client_id(),  # Registered client id
            'redirect_uri': launch_url,  # URL to return to after login
            'state': state,  # State to identify browser session
            'nonce': nonce,  # Prevent replay attacks
            'login_hint':  self._get_request_param('login_hint')  # Login hint to identify platform session
        }

        # pass back LTI message hint if we have it
        lti_message_hint = self._get_request_param('lti_message_hint')
        if lti_message_hint:
            # LTI message hint to identify LTI context within the platform
            auth_params['lti_message_hint'] = lti_message_hint

        auth_login_return_url = registration.get_auth_login_url() + "?" + urlencode(auth_params)
        return auth_login_return_url

    def _prepare_redirect(self, launch_url):
        auth_login_return_url = self._prepare_redirect_url(launch_url)
        return self.get_redirect(auth_login_return_url)

    def redirect(self, launch_url, js_redirect=False):
        """
        Calculate the redirect location to return to based on an OIDC third party initiated login request.

        :param launch_url: URL to redirect back to after the OIDC login.
        This URL must match exactly a URL white listed in the platform.
        :param js_redirect: Redirect through JS
        :return: Returns a redirect object containing the fully formed OIDC login URL.
        """
        if self._browser_allows_cookies_check_enabled:
            if not self._is_new_window_request():
                html = self.get_cookies_allowed_js_check()
                return self.get_response(html)

        redirect_obj = self._prepare_redirect(launch_url)
        if js_redirect:
            return redirect_obj.do_js_redirect()
        return redirect_obj.do_redirect()

    def get_redirect_object(self, launch_url):
        return self._prepare_redirect(launch_url)

    def validate_oidc_login(self):
        # validate Issuer
        iss = self._get_request_param('iss')
        if not iss:
            raise OIDCException('Could not find issuer')

        # validate login hint
        login_hint = self._get_request_param('login_hint')
        if not login_hint:
            raise OIDCException('Could not find login hint')

        client_id = self._get_request_param('client_id')

        # fetch registration details
        if self._tool_config.check_iss_has_one_client(iss):
            registration = self._tool_config.find_registration(iss, action=Action.OIDC_LOGIN, request=self._request)
        else:
            registration = self._tool_config.find_registration_by_params(iss, client_id, action=Action.OIDC_LOGIN,
                                                                         request=self._request)

        # check we got something
        if not registration:
            raise OIDCException("Could not find registration details")

        return registration

    def pass_params_to_launch(self, params):
        """
        Ability to pass custom params from oidc login to launch.
        """
        self._state_params = params
        return self

    def enable_check_cookies(self, main_msg=None, click_msg=None, loading_msg=None, **kwargs):
        # pylint: disable=unused-argument
        self._browser_allows_cookies_check_enabled = True
        if main_msg:
            self._cookies_unavailable_msg_main_text = main_msg
        if click_msg:
            self._cookies_unavailable_msg_click_text = click_msg
        if loading_msg:
            self._cookies_check_loading_text = loading_msg
        return self

    def disable_check_cookies(self):
        self._browser_allows_cookies_check_enabled = False
        return self

    def get_additional_login_params(self):
        """
        You may add additional custom params in your own OIDCLogin class
        :return: list
        """
        return []

    def get_cookies_allowed_js_check(self):
        protocol = 'https' if self._request.is_secure() else 'http'
        params_lst = ['iss', 'login_hint', 'target_link_uri', 'lti_message_hint',
                      'lti_deployment_id', 'client_id']
        additional_login_params = self.get_additional_login_params()
        params_lst.extend(additional_login_params)

        params = {
            'lti1p3_new_window': '1'
        }
        for param_key in params_lst:
            param_value = self._get_request_param(param_key)
            if param_value:
                params[param_key] = param_value

        page = CookiesAllowedCheckPage(params, protocol, self._cookies_unavailable_msg_main_text,
                                       self._cookies_unavailable_msg_click_text, self._cookies_check_loading_text)

        return page.get_html()

    def set_launch_data_storage(self, data_storage):
        data_storage.set_request(self._request)
        session_cookie_name = data_storage.get_session_cookie_name()
        if session_cookie_name:
            session_id = self._cookie_service.get_cookie(session_cookie_name)
            if not session_id:
                session_id = self._get_uuid()
                self._cookie_service.set_cookie(session_cookie_name, session_id, None)
            data_storage.set_session_id(session_id)
        self._session_service.set_data_storage(data_storage)
        return self

    def set_launch_data_lifetime(self, time_sec):
        self._session_service.set_launch_data_lifetime(time_sec)
        return self
