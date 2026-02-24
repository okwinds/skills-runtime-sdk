import React, { useCallback, useState } from 'react';
import './SkillCreateForm.css';
import { Button } from '../ui/Button';
import { Input, Textarea } from '../ui/Input';
import { createStudioSkill, type CreateStudioSkillBody } from '../../lib/api';

interface SkillCreateFormProps {
  sessionId: string;
  onSuccess?: () => void;
  onCancel?: () => void;
}

interface FormData {
  name: string;
  description: string;
  title: string;
  body_markdown: string;
  target_source: string;
}

interface FormErrors {
  name?: string;
  description?: string;
}

const defaultFormData: FormData = {
  name: '',
  description: '',
  title: '',
  body_markdown: '',
  target_source: '',
};

function validateForm(data: FormData): FormErrors {
  const errors: FormErrors = {};

  if (!data.name.trim()) errors.name = 'Name is required';
  if (!data.description.trim()) errors.description = 'Description is required';

  return errors;
}

export const SkillCreateForm: React.FC<SkillCreateFormProps> = ({
  sessionId,
  onSuccess,
  onCancel,
}) => {
  const [formData, setFormData] = useState<FormData>(defaultFormData);
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleChange = useCallback(
    (field: keyof FormData) =>
      (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        const value = e.target.value;
        setFormData((prev) => ({ ...prev, [field]: value }));
        if (errors[field as keyof FormErrors]) {
          setErrors((prev) => ({ ...prev, [field as keyof FormErrors]: undefined }));
        }
      },
    [errors],
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitting) return;

    setSubmitError(null);
    const validationErrors = validateForm(formData);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    setIsSubmitting(true);
    try {
      const body: CreateStudioSkillBody = {
        name: formData.name.trim(),
        description: formData.description.trim(),
        title: formData.title.trim() || undefined,
        body_markdown: formData.body_markdown.trim() || undefined,
        target_source: formData.target_source.trim() || undefined,
      };

      await createStudioSkill(sessionId, body);

      setFormData(defaultFormData);
      setErrors({});
      onSuccess?.();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to create skill');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="skill-create-form" onSubmit={handleSubmit}>
      <div className="skill-create-form__header">
        <h2 className="skill-create-form__title">Create Skill (File-Level)</h2>
        <p className="skill-create-form__subtitle">
          Create a skill file under a writable root, then refresh Skills to see it.
        </p>
      </div>

      {submitError && (
        <div className="skill-create-form__error" role="alert">
          {submitError}
        </div>
      )}

      <div className="skill-create-form__fields">
        <Input
          label="Name"
          placeholder="my-skill"
          value={formData.name}
          onChange={handleChange('name')}
          error={errors.name}
          required
        />

        <Textarea
          label="Description"
          placeholder="What does this skill do?"
          value={formData.description}
          onChange={handleChange('description')}
          error={errors.description}
          required
          rows={3}
        />

        <Input
          label="Title (optional)"
          placeholder="Nice display title"
          value={formData.title}
          onChange={handleChange('title')}
        />

        <Input
          label="Target Source (optional)"
          placeholder="Leave empty to use filesystem_sources[0]"
          value={formData.target_source}
          onChange={handleChange('target_source')}
        />

        <Textarea
          label="Body Markdown (optional)"
          placeholder="Skill body (Markdown). If empty, backend may generate a default."
          value={formData.body_markdown}
          onChange={handleChange('body_markdown')}
          rows={10}
        />
      </div>

      <div className="skill-create-form__actions">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </Button>
        )}
        <Button
          type="submit"
          variant="primary"
          isLoading={isSubmitting}
          disabled={isSubmitting}
        >
          Create Skill
        </Button>
      </div>
    </form>
  );
};
