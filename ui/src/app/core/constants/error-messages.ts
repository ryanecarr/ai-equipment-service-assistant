import { MappedError } from '../models/api-error.model';

type ErrorEntry = Omit<MappedError, 'code'>;

const ERROR_TABLE: Record<string, ErrorEntry> = {
  invalid_request: {
    title: 'Missing Information',
    message: 'Describe the reported issue before running an assessment.',
    retryable: false,
  },
  invalid_json: {
    title: 'Malformed Request',
    message: 'The request could not be read. Try submitting again.',
    retryable: true,
  },
  upstream_agent_error: {
    title: 'Assistant Unavailable',
    message: 'The diagnostic assistant could not be reached. Wait a moment and try again.',
    retryable: true,
  },
  invalid_agent_response: {
    title: 'Unexpected Response',
    message: "The assistant's output didn't match the expected format. Try rephrasing the issue, or try again.",
    retryable: true,
  },
  internal_error: {
    title: 'Server Error',
    message: 'Something went wrong on the server. Try again shortly.',
    retryable: true,
  },
  network_error: {
    title: 'Connection Failed',
    message: 'Could not reach the assessment service. Check your network connection and confirm CORS is enabled for this origin.',
    retryable: true,
  },
};

const DEFAULT_ERROR: ErrorEntry = {
  title: 'Something Went Wrong',
  message: 'An unexpected error occurred. Try again.',
  retryable: true,
};

export function mapErrorCode(code: string): MappedError {
  const entry = ERROR_TABLE[code] ?? DEFAULT_ERROR;
  return { code, ...entry };
}
