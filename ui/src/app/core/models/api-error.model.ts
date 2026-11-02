export interface ApiError {
  error: {
    code: string;
    message: string;
  };
}

export interface MappedError {
  code: string;
  title: string;
  message: string;
  retryable: boolean;
}
