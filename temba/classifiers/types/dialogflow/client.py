from google.cloud import dialogflow_v2


class Client:
    def __init__(self, credentials):
        self.credentials = credentials
        self.project_id = credentials["project_id"]

    def get_agent(self):
        # Create a client
        parent = f"projects/{self.project_id}"
        client = dialogflow_v2.AgentsClient.from_service_account_info(self.credentials)
        # Initialize request argument(s)
        request = dialogflow_v2.GetAgentRequest(parent=parent)
        return client.get_agent(request=request)

    def list_intents(self):
        # Create a client
        client = dialogflow_v2.IntentsClient.from_service_account_info(self.credentials)
        # Initialize request argument(s)
        parent = dialogflow_v2.AgentsClient.agent_path(self.project_id)
        request = dialogflow_v2.ListIntentsRequest(parent=parent)
        return client.list_intents(request=request)
